"""Contract regression tests for evidence coverage, complete table rows and fail-closed output."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_workflow import FakeDatabase, PriceRetriever, Resolver

from placidway.llm import Generator, StructuredOutputError
from placidway.models import (
    AnswerDraft,
    Coverage,
    Evidence,
    EvidenceTable,
    PriceFact,
    ResolvedRequest,
    Verification,
)
from placidway.retrieval import Embedder, Retriever
from placidway.sources import SOURCE_URLS
from placidway.workflow import Workflow


class Tokenizer:
    def encode(self, text, add_special_tokens=False, **kwargs):
        return text.split() + (["[CLS]", "[SEP]"] if add_special_tokens else [])

    def decode(self, tokens, **kwargs):
        return " ".join(tokens)


def test_table_chunks_preserve_whole_rows_and_repeat_headers():
    embedder = Embedder("unused")
    embedder._model = SimpleNamespace(tokenizer=Tokenizer())
    headers = ["Procedure", "Price", "Context"]
    rows = [[f"Treatment-{i}", f"USD-{i}", " ".join(["context"] * 90)] for i in range(4)]
    section = Evidence(
        id="table",
        source_id=6,
        url="unused",
        title="Prices",
        heading="Clinic",
        text="\n".join(" | ".join(row) for row in [headers, *rows]),
        tables=[EvidenceTable(headers=headers, rows=rows)],
    )
    chunks = embedder.chunks([section])
    assert len(chunks) > 1
    for row in rows:
        containing = [chunk[2] for chunk in chunks if " | ".join(row) in chunk[2]]
        assert len(containing) == 1 and " | ".join(headers) in containing[0]
    assert all(
        len(embedder.model.tokenizer.encode(chunk[2], add_special_tokens=True)) <= 220 for chunk in chunks
    )


async def test_coverage_broadens_once_even_when_initial_hits_exist():
    class MissingResolver(Resolver):
        checks = 0

        async def call(self, schema, *args):
            if schema is Coverage:
                self.checks += 1
                return Coverage(sufficient=False, missing=["recovery duration"])
            if schema is AnswerDraft:
                return AnswerDraft(outcome="unknown", offer_quote=True)
            return await super().call(schema, *args)

    class TrackingRetriever(PriceRetriever):
        expanded = []

        async def retrieve(self, *args, **kwargs):
            self.expanded.append(kwargs.get("expanded", False))
            return await super().retrieve(*args, **kwargs)

    generator = MissingResolver(ResolvedRequest(query="Adenocarcinoma recovery", intent="general"))
    retriever = TrackingRetriever()
    answer, _ = await Workflow(FakeDatabase(), retriever, generator, InMemorySaver()).run(
        str(uuid4()), "What about recovery time?", "en", str(uuid4()), str(uuid4())
    )
    assert retriever.expanded == [False, True] and generator.checks == 2
    assert answer["outcome"] == "unknown"


async def test_invalid_schema_fallback_is_checkpointed_but_network_errors_propagate():
    class BrokenGenerator:
        async def call(self, *args):
            raise StructuredOutputError("unvalidated content")

    workflow = Workflow(FakeDatabase(), PriceRetriever(), BrokenGenerator(), InMemorySaver())
    key = str(uuid4())
    answer, _ = await workflow.run(key, "Question", "es", str(uuid4()), str(uuid4()))
    history = await workflow.history(key)
    assert len(history) == 2 and history[-1]["content"] == answer["content"]
    assert "validada" in answer["content"] and "unvalidated content" not in answer["content"]

    class NetworkFailure:
        async def call(self, *args):
            raise ConnectionError("provider unavailable")

    with pytest.raises(ConnectionError):
        await Workflow(FakeDatabase(), PriceRetriever(), NetworkFailure(), InMemorySaver()).run(
            str(uuid4()), "Question", "en", str(uuid4()), str(uuid4())
        )


async def test_schema_repair_has_a_single_shared_budget():
    class BadClient:
        calls = 0

        async def ainvoke(self, *args):
            self.calls += 1
            return SimpleNamespace(content="not JSON", usage_metadata={})

    generator = Generator(SimpleNamespace())
    generator._client = BadClient()
    # Bypass provider configuration only in this deliberately local client fixture.
    generator.config = SimpleNamespace(generation_ready=True)
    budget = [1]
    with pytest.raises(StructuredOutputError):
        await generator.call(Verification, "rules", {}, budget)
    assert generator._client.calls == 2 and budget == [0]
    with pytest.raises(StructuredOutputError):
        await generator.call(Verification, "rules", {}, budget)
    assert generator._client.calls == 3


async def test_cost_price_budget_is_independent_of_text_hit_order():
    sections = [
        Evidence(
            id=f"s2:{i}",
            source_id=2,
            url=SOURCE_URLS[2],
            title="Prices",
            heading="Cost",
            text="support " * 3000,
        )
        for i in range(4)
    ]
    prices = [
        PriceFact(
            id=f"p2:{i}", source_id=2, evidence_id=section.id, treatment="Adenocarcinoma Cancer", amount=100
        )
        for i, section in enumerate(sections)
    ]

    class BudgetDatabase:
        async def all(self, query, params):
            return [
                {"payload": item.model_dump(mode="json")}
                for item in (prices if "FROM price_facts" in query else sections)
            ]

    class OrderedRetriever(Retriever):
        async def search(self, query, version, mode, **kwargs):
            order = sections if mode == "dense" else list(reversed(sections))
            return [{"evidence_id": section.id} for section in order]

    retriever = OrderedRetriever(BudgetDatabase(), None)
    request = ResolvedRequest(query="adenocarcinoma cost", treatment="adenocarcinoma", intent="cost")
    results = [await retriever.retrieve(request, "frozen", mode) for mode in ("dense", "bm25", "hybrid")]
    selected = [[price.id for price in result[1]] for result in results]
    assert 0 < len(selected[0]) < len(prices)
    assert selected[0] == selected[1] == selected[2]


async def test_generic_therapy_word_does_not_match_every_clinic_price():
    sections = [
        Evidence(
            id=f"s{source}:price",
            source_id=source,
            url=SOURCE_URLS[source],
            title="Prices",
            heading="Cost",
            text="Documented price",
        )
        for source in (2, 7)
    ]
    prices = [
        PriceFact(
            id="p2:1",
            source_id=2,
            evidence_id=sections[0].id,
            treatment="Adenocarcinoma Cancer",
            clinic="ITC Immunity Therapy Center",
            amount=18995,
        ),
        PriceFact(
            id="p7:13",
            source_id=7,
            evidence_id=sections[1].id,
            treatment="Ozone Therapy",
            clinic="Other clinic",
            country="Turkey",
            amount=100,
        ),
    ]

    class PriceDatabase:
        async def all(self, query, params):
            return [
                {"payload": item.model_dump(mode="json")}
                for item in (prices if "FROM price_facts" in query else sections)
            ]

    class NoTextHits(Retriever):
        async def search(self, *args, **kwargs):
            return []

    _, candidates, _ = await NoTextHits(PriceDatabase(), None).retrieve(
        ResolvedRequest(
            query="Ozone therapy in Turkey and Mexico", treatment="ozone therapy", intent="comparison"
        ),
        "frozen",
        "dense",
    )
    assert [price.id for price in candidates] == ["p7:13"]
