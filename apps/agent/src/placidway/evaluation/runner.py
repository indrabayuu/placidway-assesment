import asyncio
import json
import re
import statistics
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from deepeval.test_case import ConversationalTestCase, LLMTestCase, Turn
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from placidway.config import settings
from placidway.db import Database
from placidway.evaluation.judge import DecisionJudge
from placidway.evaluation.metrics import ConversationDecisionMetric, make_metrics, retrieval_metrics
from placidway.evaluation.provenance import finish_round, provenance, record_round, require_calibration
from placidway.llm import Generator
from placidway.models import AnswerDraft, Evidence, PriceFact, ResolvedRequest
from placidway.retrieval import Embedder, Retriever
from placidway.sources import allowed_url
from placidway.workflow import Workflow, render, validate_draft

DATASET = Path(__file__).resolve().parents[3] / "tests/evals/.dataset.json"


def load_dataset():
    return json.loads(DATASET.read_text())


async def verify_corpus(db):
    manifest = json.loads((DATASET.parent / "corpus-manifest.json").read_text())["sources"]
    version = await db.active_version()
    if not version:
        raise RuntimeError("Ingest content before evaluation")
    rows = await db.all(
        "SELECT source_id,content_hash,extractor_version FROM source_versions WHERE version=%s", (version,)
    )
    if {str(r["source_id"]): r["content_hash"] for r in rows} != {
        k: v["content_hash"] for k, v in manifest.items()
    }:
        raise RuntimeError(
            "Corpus differs from the labelled snapshot. Audit/relabel changes before evaluating."
        )
    if any(
        row["extractor_version"] != manifest[str(row["source_id"])].get("extractor_version") for row in rows
    ):
        raise RuntimeError("Extractor revision differs from the audited evaluation manifest")
    return version


def deterministic_checks(case, answer):
    diagnostics = answer.get("diagnostics", {})
    used = set(diagnostics.get("used_price_ids", []))
    missing = set(case.get("required_price_ids", [])) - used
    evidence = [Evidence.model_validate(row) for row in diagnostics.get("evidence", [])]
    prices = [PriceFact.model_validate(row) for row in diagnostics.get("prices", [])]
    facts = {fact.id: fact for fact in prices}
    ev = {section.id: section for section in evidence}
    citations = answer["citations"]
    cited = {row["id"] for row in citations}
    citation_valid = all(
        row["id"] in ev and row["url"] == ev[row["id"]].url and allowed_url(row["url"]) for row in citations
    )
    coverage = (
        all(bool(set(group) & cited) for group in case.get("essential_groups", []))
        if answer["outcome"] in ("answered", "partial")
        else True
    )
    draft = AnswerDraft.model_validate(
        diagnostics.get("accepted_draft", diagnostics.get("draft", {"outcome": answer["outcome"]}))
    )
    association = not validate_draft(
        draft,
        evidence,
        prices,
        ResolvedRequest.model_validate(diagnostics.get("request", {"query": case["question"]})),
    )
    price_values = True
    remaining_content = answer["content"]
    for key in used:
        if key not in facts:
            price_values = False
            continue
        fact = facts[key]
        block = AnswerDraft(outcome="answered", blocks=[{"kind": "price", "price_id": key}])
        expected_text, _ = render(block, evidence, prices, answer["response_language"])
        approved_fragment = expected_text.rsplit(" [", 1)[0]
        price_values &= approved_fragment in answer["content"] and fact.evidence_id in cited
        remaining_content = remaining_content.replace(approved_fragment, "")
    # Inspect published content independently of draft/used-ID diagnostics, including safe fallbacks.
    price_values &= not bool(
        re.search(r"\$|€|£|\b(?:USD|EUR|MXN|dollars|dólares)\b", remaining_content, re.I)
    )
    # Independent source labels protect against a corrupted numeric record, not just an incorrect ID.
    labelled_values = all(
        key in facts
        and all(facts[key].model_dump(mode="json").get(field) == value for field, value in expected.items())
        for key, expected in case.get("expected_price_facts", {}).items()
    )
    return {
        "expected_outcome": answer["outcome"] in case["expected_outcomes"],
        "required_price_records": not missing,
        "has_required_citation": bool(citations)
        if case["relevant_evidence_ids"] and answer["outcome"] in ("answered", "partial")
        else True,
        "citation_allowlist_and_identity": citation_valid,
        "essential_evidence_cited": coverage,
        "price_values_context_and_qualifiers": price_values,
        "labelled_price_values": labelled_values,
        "fact_associations": association,
        "validation_passed": not validate_draft(
            draft,
            evidence,
            prices,
            ResolvedRequest.model_validate(diagnostics.get("request", {"query": case["question"]})),
        ),
        "missing_price_ids": sorted(missing),
    }


def test_case(case, answer):
    diagnostics = answer.get("diagnostics", {})
    return LLMTestCase(
        input=case["question"],
        actual_output=answer["content"],
        expected_output="; ".join(case["required_facts"]),
        retrieval_context=[json.dumps(e, ensure_ascii=False) for e in diagnostics.get("evidence", [])],
        metadata={**case, "price_facts": diagnostics.get("prices", [])},
        name=case["id"],
    )


async def evaluate_case(workflow, case, judge=None):
    answer, _ = await workflow.run(
        str(uuid4()), case["question"], case["response_language"], str(uuid4()), str(uuid4())
    )
    checks = deterministic_checks(case, answer)
    scores = {}
    if judge:
        for metric in make_metrics(judge):
            scores[metric.criterion] = await metric.a_measure(test_case(case, answer))
    passed = all(value for key, value in checks.items() if key != "missing_price_ids") and all(
        v >= 0.8 for v in scores.values()
    )
    return {
        "id": case["id"],
        "question": case["question"],
        "passed": passed,
        "checks": checks,
        "scores": scores,
        "answer": answer,
        "expected": case,
    }


@lru_cache(maxsize=1)
def evaluation_embedder(model_name):
    return Embedder(model_name)


async def resources(mode="dense"):
    config = settings().model_copy(update={"retrieval_mode": mode})
    db = Database(config)
    await db.open()
    try:
        version = await verify_corpus(db)
    except Exception:
        await db.close()
        raise
    retriever = Retriever(db, evaluation_embedder(config.embedding_model))
    workflow = Workflow(db, retriever, Generator(config), AsyncPostgresSaver(db.pool), pinned_version=version)
    return config, db, retriever, workflow


def report(folder, results, metadata):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "results.json").write_text(
        json.dumps({"metadata": metadata, "results": results}, ensure_ascii=False, indent=2)
    )
    durations = [r["answer"]["diagnostics"].get("seconds", 0) for r in results if "answer" in r]
    passed = sum(r.get("passed", False) for r in results)
    lines = [
        "# Local evaluation report",
        "",
        f"Passed: {passed}/{len(results)}",
        "",
        f"Metadata: `{json.dumps(metadata)}`",
        "",
    ]
    if durations:
        ordered = sorted(durations)
        lines += [
            f"Latency p50: {statistics.median(durations):.2f}s; p95: {ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]:.2f}s.",
            "",
        ]
    for row in results:
        lines += [
            f"## {row['id']} — {'PASS' if row.get('passed') else 'FAIL'}",
            "",
            row.get("question", ""),
            "",
        ]
        if "error" in row:
            lines += ["Operational error: " + row["error"], ""]
        else:
            lines += [
                row["answer"]["content"],
                "",
                "Checks: `" + json.dumps(row["checks"]) + "`",
                "",
                "Scores: `" + json.dumps(row["scores"]) + "`",
                "",
            ]
    (folder / "summary.md").write_text("\n".join(lines))


async def run_benchmark(
    split="dev", mode="dense", limit=0, judge_enabled=True, improvement_round=0, failure="", change=""
):
    if split not in ("dev", "heldout", "all"):
        raise ValueError("split must be dev, heldout, or all")
    if mode not in ("dense", "bm25", "hybrid"):
        raise ValueError("Invalid retrieval mode")
    config = settings()
    if judge_enabled:
        require_calibration(config)
    ledger = record_round(config, improvement_round, failure, change) if improvement_round else None
    config, db, retriever, workflow = await resources(mode)
    pinned_version = await verify_corpus(db)
    metadata = {
        **provenance(config, pinned_version),
        "split": split,
        "mode": mode,
        "judge_enabled": judge_enabled,
        "run_type": "improvement" if improvement_round else "verification",
        "improvement_round": improvement_round or None,
        "label_provenance": "AI-curated/source-verified",
    }
    cases = [c for c in load_dataset()["cases"] if split == "all" or c["split"] == split]
    if limit:
        cases = cases[:limit]
    judge = DecisionJudge(config) if judge_enabled else None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = config.reports_dir / "evals" / f"{stamp}-{split}-{mode}"
    results = []
    semaphore = asyncio.Semaphore(config.evaluation_concurrency)

    async def run(case):
        async with semaphore:
            try:
                result = await evaluate_case(workflow, case, judge)
            except Exception as exc:
                result = {
                    "id": case["id"],
                    "question": case["question"],
                    "passed": False,
                    "error": type(exc).__name__,
                    "error_category": "infrastructure_or_protocol",
                }
            results.append(result)
            results.sort(key=lambda r: r["id"])
            report(
                folder,
                results,
                metadata,
            )
            print(f"{case['id']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)

    try:
        await asyncio.gather(*(run(case) for case in cases))
        if ledger:
            finish_round(ledger, improvement_round, folder, results)
        print(f"Local report: {folder}", flush=True)
    finally:
        await db.close()
    return results


async def compare():
    config, db, retriever, workflow = await resources()
    version = workflow.pinned_version
    cases = [c for c in load_dataset()["cases"] if c["split"] == "dev" and c["relevant_evidence_ids"]]
    results = {}
    try:
        for mode in ("dense", "bm25", "hybrid"):
            rows = []
            for case in cases:
                _, prices, hits = await retriever.retrieve(
                    ResolvedRequest(
                        query=case["question"],
                        treatment=case.get("retrieval_treatment", ""),
                        intent=case.get("retrieval_intent", "general"),
                    ),
                    version,
                    mode,
                )
                rows.append(
                    {
                        "id": case["id"],
                        "price_candidates": [fact.id for fact in prices],
                        **{
                            str(k): retrieval_metrics(
                                [h["evidence_id"] for h in hits],
                                case["relevant_evidence_ids"],
                                k,
                                required_groups=case["essential_groups"],
                            )
                            for k in (3, 5)
                        },
                    }
                )
            means = {
                str(k): {
                    metric: statistics.mean(row[str(k)][metric] for row in rows)
                    for metric in ("recall", "ndcg", "mrr", "essential_coverage")
                }
                for k in (3, 5)
            }
            results[mode] = {"means": means, "cases": rows}
        folder = config.reports_dir / "retrieval"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "comparison.json").write_text(
            json.dumps(
                {
                    **provenance(config, version),
                    "version": version,
                    "query_protocol": "identical original English questions; structured lookup enabled",
                    "results": results,
                },
                indent=2,
            )
        )
        print(json.dumps({mode: row["means"] for mode, row in results.items()}, indent=2))
    finally:
        await db.close()


async def evaluate_conversation(workflow, case, judge):
    conversation_id = str(uuid4())
    turns = []
    answers = []
    for index, (question, expectation) in enumerate(case["turns"]):
        language = case.get("languages", ["en"] * len(case["turns"]))[index]
        answer, _ = await workflow.run(conversation_id, question, language, str(uuid4()), str(uuid4()))
        answers.append(answer)
        turns += [
            Turn(role="user", content=question),
            Turn(
                role="assistant",
                content=answer["content"],
                retrieval_context=[json.dumps(e) for e in answer.get("diagnostics", {}).get("evidence", [])],
            ),
        ]
    test = ConversationalTestCase(
        turns=turns,
        name=case["id"],
        metadata={
            "expected_turns": case["turns"],
            "languages": case.get("languages"),
            "answers": answers,
            "price_facts_by_turn": [a.get("diagnostics", {}).get("prices", []) for a in answers],
            "turn_checks": [
                deterministic_checks(
                    {
                        "question": case["turns"][index][0],
                        "expected_outcomes": case["turn_expected_outcomes"][index],
                        "required_price_ids": case["turn_price_ids"][index],
                        "relevant_evidence_ids": [],
                        "expected_price_facts": case["expected_price_facts_by_turn"][index],
                    },
                    answer,
                )
                for index, answer in enumerate(answers)
            ],
        },
    )
    metric = ConversationDecisionMetric(judge)
    await metric.a_measure(test)
    return test, metric


async def run_conversations(split="dev", mode="hybrid"):
    if split not in ("dev", "heldout", "all"):
        raise ValueError("Invalid split")
    require_calibration(settings())
    config, db, _, workflow = await resources(mode)
    judge = DecisionJudge(config)
    results = []
    folder = (
        config.reports_dir
        / "conversations"
        / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + split)
    )
    folder.mkdir(parents=True, exist_ok=True)
    try:
        for case in load_dataset()["conversations"]:
            if split != "all" and case["split"] != split:
                continue
            try:
                test, metric = await evaluate_conversation(workflow, case, judge)
                row = {
                    "id": case["id"],
                    "passed": metric.is_successful()
                    and all(
                        all(value for key, value in checks.items() if key != "missing_price_ids")
                        for checks in test.metadata["turn_checks"]
                    ),
                    "score": metric.score,
                    "raw_judgment": metric.raw_judgment,
                    "answers": test.metadata["answers"],
                    "turns": [turn.model_dump(mode="json") for turn in test.turns],
                    "expected": case,
                    "turn_checks": test.metadata["turn_checks"],
                }
            except Exception as exc:
                row = {"id": case["id"], "passed": False, "error": type(exc).__name__}
            results.append(row)
            (folder / "results.json").write_text(
                json.dumps(
                    {
                        **provenance(config, workflow.pinned_version),
                        "mode": mode,
                        "split": split,
                        "run_type": "verification",
                        "results": results,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            print(f"{case['id']}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)
        print(f"Local conversation report: {folder}")
    finally:
        await db.close()
    return results
