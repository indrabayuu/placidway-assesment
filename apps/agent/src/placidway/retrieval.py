import asyncio
import re
import threading

import numpy as np
from rank_bm25 import BM25Okapi

from placidway.ingestion import digest
from placidway.models import Evidence, PriceFact, ResolvedRequest
from placidway.sources import treatment_source


def lexical(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class Embedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._lock = threading.RLock()

    @property
    def model(self):
        with self._lock:
            if self._model is None:
                import torch
                from sentence_transformers import SentenceTransformer

                torch.set_num_threads(1)
                self._model = SentenceTransformer(self.model_name, device="cpu")
            return self._model

    def encode(self, texts):
        with self._lock:
            return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False).astype(
                np.float32
            )

    def chunks(self, sections: list[Evidence]):
        with self._lock:
            return self._chunks(sections)

    def _chunks(self, sections: list[Evidence]):
        tokenizer = self.model.tokenizer
        chunks = []
        for section in sections:
            prefix = "\n".join(section.heading_path or [section.title, section.heading]) + "\n"
            prefix_ids = tokenizer.encode(prefix, add_special_tokens=False)[:65]
            table_lines = {
                " | ".join(row) for table in section.tables for row in [table.headers, *table.rows]
            }
            prose = "\n".join(line for line in section.text.splitlines() if line not in table_lines)
            body = tokenizer.encode(prose, add_special_tokens=False, verbose=False)
            budget = 218 - len(prefix_ids)
            for start in range(0, len(body), budget - 32):
                text = tokenizer.decode(prefix_ids + body[start : start + budget], skip_special_tokens=True)
                if len(tokenizer.encode(text, add_special_tokens=True)) > 220:
                    raise ValueError("Embedding chunk exceeded tokenizer budget")
                chunks.append((section.id + ":" + digest(text)[:12], section.id, text))
                if start + budget >= len(body):
                    break
            prefix_text = tokenizer.decode(prefix_ids, skip_special_tokens=True) + "\n"
            for table in section.tables:
                header = " | ".join(table.headers)
                packed = []
                for row in table.rows:
                    line = " | ".join(row)
                    if (
                        len(tokenizer.encode(prefix_text + header + "\n" + line, add_special_tokens=True))
                        > 220
                    ):
                        raise ValueError("Complete table row and header exceed the embedding budget")
                    candidate = prefix_text + header + "\n" + "\n".join([*packed, line])
                    if len(tokenizer.encode(candidate, add_special_tokens=True)) > 220:
                        text = prefix_text + header + "\n" + "\n".join(packed)
                        chunks.append((section.id + ":" + digest(text)[:12], section.id, text))
                        packed = []
                    packed.append(line)
                if packed:
                    text = prefix_text + header + "\n" + "\n".join(packed)
                    chunks.append((section.id + ":" + digest(text)[:12], section.id, text))
        return chunks


class Retriever:
    def __init__(self, db, embedder):
        self.db, self.embedder = db, embedder
        self._cache_version = None
        self._rows = []
        self._bm25 = None

    async def corpus(self, version):
        if self._cache_version != version:
            rows = await self.db.all(
                "SELECT id,evidence_id,text FROM chunks WHERE version=%s ORDER BY id", (version,)
            )
            self._rows = rows
            self._bm25 = BM25Okapi([lexical(row["text"]) for row in rows]) if rows else None
            self._cache_version = version
        return self._rows

    async def search(self, query: str, version: str, mode="dense", k=5, source_id=None):
        dense = []
        sparse = []
        if mode in ("dense", "hybrid"):
            vector = (await asyncio.to_thread(self.embedder.encode, [query]))[0]
            dense = await self.db.all(
                "SELECT id,evidence_id,text,1-(embedding <=> %s) AS score FROM chunks WHERE version=%s AND (%s::text IS NULL OR id LIKE %s) ORDER BY embedding <=> %s,id LIMIT %s",
                (
                    vector,
                    version,
                    str(source_id) if source_id else None,
                    f"s{source_id}:%",
                    vector,
                    max(k, 20) if mode == "hybrid" else k,
                ),
            )
        if mode in ("bm25", "hybrid"):
            rows = await self.corpus(version)
            if self._bm25:
                scores = self._bm25.get_scores(lexical(query))
                candidates = [
                    i for i in range(len(rows)) if not source_id or rows[i]["id"].startswith(f"s{source_id}:")
                ]
                indices = sorted(candidates, key=lambda i: (-float(scores[i]), rows[i]["id"]))[: max(k, 20)]
                sparse = [{**rows[i], "score": float(scores[i])} for i in indices]
        if mode == "dense":
            return dense[:k]
        if mode == "bm25":
            return sparse[:k]
        fused, lookup = {}, {}
        for ranking in (dense, sparse):
            for rank, row in enumerate(ranking, 1):
                key = row["id"]
                lookup[key] = row
                fused[key] = fused.get(key, 0) + 1 / (60 + rank)
        return [
            {**lookup[key], "score": fused[key]}
            for key in sorted(fused, key=lambda key: (-fused[key], key))[:k]
        ]

    async def search_documents(self, query: str, version: str):
        """Query-driven hybrid retrieval; no resolver-generated entity fields."""
        import json

        import tiktoken

        scope = treatment_source(query)
        hits = await self.search(query, version, self.db.config.retrieval_mode, k=5, source_id=scope)
        ids = list(dict.fromkeys(hit["evidence_id"] for hit in hits))
        all_prices = [
            PriceFact.model_validate(row["payload"])
            for row in await self.db.all(
                "SELECT payload FROM price_facts WHERE version=%s ORDER BY id", (version,)
            )
        ]
        query_terms = set(lexical(query))
        generic = {
            "cancer",
            "treatment",
            "therapy",
            "package",
            "programme",
            "program",
            "price",
            "cost",
            "alternative",
            "clinic",
            "care",
            "medical",
        }
        prices = [
            fact
            for fact in all_prices
            if fact.evidence_id in ids
            or (scope and fact.source_id == scope)
            or bool((set(lexical(fact.treatment)) - generic) & query_terms)
        ]
        for fact in prices:
            for key in [fact.evidence_id, *fact.context_evidence_ids]:
                if key not in ids:
                    ids.append(key)
        if not ids:
            return [], [], hits
        rows = await self.db.all(
            "SELECT payload FROM evidence WHERE version=%s AND id=ANY(%s)", (version, ids)
        )
        mapping = {row["payload"]["id"]: Evidence.model_validate(row["payload"]) for row in rows}
        # Page introductions provide usable context when metadata-heavy tiny chunks
        # rank highly. Prefer actual explanatory text over treating a title as proof.
        sources = sorted({section.source_id for section in mapping.values()})
        context = await self.db.all(
            "SELECT payload FROM evidence WHERE version=%s AND source_id=ANY(%s)", (version, sources)
        )
        introductions = [
            Evidence.model_validate(row["payload"])
            for row in context
            if row["payload"]["heading"].lower().startswith(("about ", "overview"))
        ]
        ordered = list(dict.fromkeys([e.id for e in introductions] + ids))
        mapping.update({section.id: section for section in introductions})
        encoder = tiktoken.get_encoding("o200k_harmony")
        selected, retained, used = [], [], 0
        for key in ordered:
            section = mapping.get(key)
            if section is None:
                continue
            section_prices = [fact for fact in prices if fact.evidence_id == key]
            size = len(
                encoder.encode(
                    json.dumps(
                        {
                            "evidence": section.model_dump(mode="json"),
                            "prices": [f.model_dump(mode="json") for f in section_prices],
                        },
                        ensure_ascii=False,
                    )
                )
            )
            if used + size <= 8000:
                selected.append(section)
                retained.extend(section_prices)
                used += size
        return selected, retained, hits

    async def retrieve(self, request: ResolvedRequest, version, mode, expanded=False):
        scope = treatment_source(request.query + " " + request.treatment)
        hits = await self.search(
            request.query,
            version,
            mode,
            k=10 if expanded else 5,
            source_id=scope if request.intent != "comparison" else None,
        )
        evidence_ids = list(dict.fromkeys(row["evidence_id"] for row in hits))
        all_prices = [
            PriceFact.model_validate(row["payload"])
            for row in await self.db.all(
                "SELECT payload FROM price_facts WHERE version=%s ORDER BY id", (version,)
            )
        ]

        # Candidate generation is broad; exact entity/programme checks also run on selected answers.
        def treatment_terms(value):
            return {word[:-1] if len(word) > 3 and word.endswith("s") else word for word in lexical(value)}

        terms = treatment_terms(request.treatment or request.query) - {
            "cancer",
            "treatment",
            "alternative",
            "the",
            "cost",
            "how",
            "much",
            "in",
            "itc",
            "therapy",
            "therapie",
            "external",
            "additional",
            "optional",
            "programme",
            "program",
            "package",
            "procedure",
            "care",
        }
        prices = []
        question_terms = set(lexical(request.query))
        topics = {
            "ambulance",
            "imaging",
            "ct",
            "lak",
            "ethanol",
            "intratumoral",
            "blood",
            "antibody",
            "mca",
            "specialist",
        }
        for fact in all_prices:
            # Generic words in a clinic name ("Immunity Therapy Center") are not
            # evidence that it offers the requested treatment. Match the treatment itself.
            match = bool(terms) and terms <= treatment_terms(fact.treatment + " " + fact.description)
            if request.intent not in ("cost", "comparison"):
                relevant_addon = bool(
                    question_terms & topics & set(lexical(fact.treatment + " " + fact.description))
                )
                if fact.kind == "addon" and not (match or relevant_addon):
                    continue
                programme = request.programme.lower().replace("-", "")
                if (
                    fact.kind == "programme"
                    and programme in ("inpatient", "outpatient")
                    and programme not in fact.programme.lower().replace("-", "")
                ):
                    continue
            if (
                request.intent in ("cost", "comparison", "inclusions")
                or (request.intent == "general" and scope)
            ) and (match or not terms or fact.source_id == scope):
                prices.append(fact)
        for fact in prices:
            for key in [fact.evidence_id, *fact.context_evidence_ids]:
                if key not in evidence_ids:
                    evidence_ids.append(key)
        if expanded:
            query_terms = set(lexical(request.query))
            parents = await self.db.all("SELECT payload FROM evidence WHERE version=%s", (version,))
            matches = sorted(
                parents, key=lambda r: len(query_terms & set(lexical(r["payload"]["heading"]))), reverse=True
            )
            for row in matches[:5]:
                if row["payload"]["id"] not in evidence_ids:
                    evidence_ids.append(row["payload"]["id"])
        if not evidence_ids:
            return [], prices, hits
        rows = await self.db.all(
            "SELECT payload FROM evidence WHERE version=%s AND id=ANY(%s)", (version, evidence_ids)
        )
        mapping = {row["payload"]["id"]: Evidence.model_validate(row["payload"]) for row in rows}
        evidence = [mapping[key] for key in evidence_ids if key in mapping]
        # Match the GPT-OSS token budget, including all required price evidence.
        import tiktoken

        encoder = tiktoken.get_encoding("o200k_harmony")
        selected, used = [], 0
        # Reserve exact serialized price records with their complete supporting parents first.
        price_parents = {fact.evidence_id for fact in prices}
        if request.intent in ("cost", "comparison"):
            # A retriever's hit order must not change which structured prices fit the shared budget.
            price_order = {key: index for index, key in enumerate(sorted(price_parents))}
            evidence.sort(key=lambda section: price_order.get(section.id, len(price_order)))
        retained_prices = []
        for section in evidence:
            section_prices = [fact for fact in prices if fact.evidence_id == section.id]
            import json

            size = len(
                encoder.encode(
                    json.dumps(
                        {
                            "evidence": section.model_dump(),
                            "prices": [fact.model_dump(mode="json") for fact in section_prices],
                        },
                        ensure_ascii=False,
                    )
                )
            )
            if used + size <= 8000:
                selected.append(section)
                retained_prices.extend(section_prices)
                used += size
        return selected, retained_prices, hits
