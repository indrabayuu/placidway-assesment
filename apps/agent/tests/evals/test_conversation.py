import os

import pytest
from deepeval import assert_test

from placidway.evaluation.judge import DecisionJudge
from placidway.evaluation.runner import evaluate_conversation, load_dataset, resources

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_EVALS") != "1", reason="Explicit live evaluation opt-in required"
    ),
]
cases = [
    c for c in load_dataset()["conversations"] if os.environ.get("EVAL_SPLIT", "dev") in ("all", c["split"])
]
if os.environ.get("EVAL_LIMIT"):
    cases = cases[: int(os.environ["EVAL_LIMIT"])]


@pytest.mark.parametrize("case", cases, ids=lambda c: c["id"])
async def test_conversation_context(case, eval_capture):
    config, db, _, workflow = await resources(os.environ.get("RETRIEVAL_MODE", "dense"))
    try:
        test, metric = await evaluate_conversation(workflow, case, DecisionJudge(config))
        checks_ok = all(
            all(value for key, value in checks.items() if key != "missing_price_ids")
            for checks in test.metadata["turn_checks"]
        )
        eval_capture(
            {
                "id": case["id"],
                "passed": metric.is_successful() and checks_ok,
                "score": metric.score,
                "raw_judgment": metric.raw_judgment,
                "answers": test.metadata["answers"],
                "turns": [turn.model_dump(mode="json") for turn in test.turns],
                "expected": case,
                "turn_checks": test.metadata["turn_checks"],
            },
            kind="conversation",
            corpus=workflow.pinned_version,
        )
        assert all(
            all(value for key, value in checks.items() if key != "missing_price_ids")
            for checks in test.metadata["turn_checks"]
        )
        assert_test(test, [metric])
    except Exception as exc:
        if "test" not in locals():
            eval_capture(
                {
                    "id": case["id"],
                    "passed": False,
                    "error": type(exc).__name__,
                    "error_category": "infrastructure_or_protocol",
                },
                kind="conversation",
                corpus=workflow.pinned_version,
            )
        raise
    finally:
        await db.close()
