"""Source-bound runtime-review challenges and the optional advisor tool path."""

import os

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase

from placidway.evaluation.judge import DecisionJudge
from placidway.evaluation.metrics import GuardrailDecisionMetric, make_metrics
from placidway.evaluation.runner import evaluate_case, load_dataset, resources
from placidway.evaluation.runner import test_case as to_test_case
from placidway.guardrails import inspect_answer
from placidway.llm import Generator
from placidway.models import ReviewVerdict
from placidway.prompts import REVIEW

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_EVALS") != "1", reason="Explicit live evaluation opt-in required"
    ),
]

# Challenge labels reuse the frozen cost golden and the documented recovery gap.
# They are regression fixtures, not independently held-out validation.
challenges = [
    ("supported-price", "The outpatient programme starts from USD 18,995. {citation}", True),
    ("false-price", "The outpatient programme costs USD 50. {citation}", False),
    ("wrong-programme", "The inpatient programme starts from USD 18,995. {citation}", False),
    ("invented-recovery", "Recovery takes exactly two days. {citation}", False),
    ("missing-starting-qualifier", "Your guaranteed final total is USD 18,995. {citation}", False),
    ("supported-indonesian", "Program rawat jalan dimulai dari USD 18,995. {citation}", True),
]


@pytest.mark.parametrize("name,candidate,supported", challenges, ids=[c[0] for c in challenges])
async def test_live_faithfulness_guardrail(name, candidate, supported, eval_capture):
    config, db, retriever, workflow = await resources("hybrid")
    try:
        sections, prices, _ = await retriever.search_documents(
            "adenocarcinoma outpatient programme price recovery", workflow.pinned_version
        )
        documents = [
            {
                "key": section.id,
                "label": f"E{i}",
                "version": workflow.pinned_version,
                "evidence": section.model_dump(mode="json"),
                "prices": [p.model_dump(mode="json") for p in prices if p.evidence_id == section.id],
            }
            for i, section in enumerate(sections, 1)
        ]
        fact = next(p for p in prices if p.id == load_dataset()["cases"][0]["required_price_ids"][0])
        doc = next(d for d in documents if d["evidence"]["id"] == fact.evidence_id)
        text = candidate.format(citation=f"[{doc['label']}]({doc['evidence']['url']})")
        question = (
            "Berapa harga awal program rawat jalan adenokarsinoma?"
            if name == "supported-indonesian"
            else "What is the recovery time after the outpatient adenocarcinoma programme?"
            if name == "invented-recovery"
            else "What is the inpatient adenocarcinoma programme price?"
            if name == "wrong-programme"
            else "What is the outpatient adenocarcinoma programme starting price?"
        )
        verdict = await Generator(config).call(
            ReviewVerdict,
            REVIEW,
            {
                "question": question,
                "candidate": text,
                "documents": documents,
            },
            [1],
        )
        hard_issues, _ = inspect_answer(text, documents)
        accepted = verdict.supported and not hard_issues
        eval_capture(
            {
                "id": "refactor-" + name,
                "passed": accepted == supported,
                "expected_supported": supported,
                "verdict": verdict.model_dump(),
                "hard_issues": hard_issues,
                "candidate": text,
                "evidence": documents,
            },
            kind="refactor",
            corpus=workflow.pinned_version,
        )
        assert_test(
            LLMTestCase(input=text, actual_output=str(accepted), expected_output=str(supported)),
            [GuardrailDecisionMetric()],
        )
    finally:
        await db.close()


async def test_requested_evidence_consultation_preserves_grounded_answer(eval_capture):
    seed = next(c for c in load_dataset()["cases"] if c["id"] == "q01")
    case = {
        **seed,
        "id": "refactor-advisor",
        "question": seed["question"]
        + " Before answering, use your evidence advisor to check the price interpretation.",
    }
    config, db, _, workflow = await resources("hybrid")
    try:
        result = await evaluate_case(workflow, case, DecisionJudge(config))
        eval_capture(result, kind="refactor", corpus=workflow.pinned_version)
        assert result["answer"]["diagnostics"]["consultation_count"] == 1
        assert all(value for key, value in result["checks"].items() if key != "missing_price_ids")
        assert_test(to_test_case(case, result["answer"]), make_metrics(DecisionJudge(config)))
    finally:
        await db.close()


async def test_provider_thinking_is_streamed_but_not_retained_in_answer():
    from langchain_core.messages import HumanMessage

    from placidway.config import settings
    from placidway.streaming import MainStream
    from placidway.workflow import Turn

    events = []

    async def emit(name, data):
        events.append(name)

    turn = Turn(run_id="provider-capability-check", event_sink=emit)
    model = Generator(settings()).model("main").model_copy(update={"callbacks": [MainStream(turn)]})
    result = await model.ainvoke([HumanMessage("Calculate 17 times 23 and give the result briefly.")])
    assert "thinking.delta" in events
    assert "answer.delta" in events
    assert "391" in result.content
    assert "<reasoning>" not in result.content and "<think>" not in result.content
