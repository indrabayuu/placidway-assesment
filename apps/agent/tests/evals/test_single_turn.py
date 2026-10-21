"""Live evals are opt-in; run with RUN_LIVE_EVALS=1 uv run deepeval test run ..."""

import os

import pytest
from deepeval import assert_test

from placidway.evaluation.judge import DecisionJudge
from placidway.evaluation.metrics import make_metrics
from placidway.evaluation.runner import evaluate_case, load_dataset, resources
from placidway.evaluation.runner import test_case as to_test_case

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_EVALS") != "1", reason="Explicit live evaluation opt-in required"
    ),
]
cases = [c for c in load_dataset()["cases"] if os.environ.get("EVAL_SPLIT", "dev") in ("all", c["split"])]
if os.environ.get("EVAL_LIMIT"):
    cases = cases[: int(os.environ["EVAL_LIMIT"])]


@pytest.mark.parametrize("case", cases, ids=lambda c: c["id"])
async def test_grounded_response(case, eval_capture):
    config, db, _, workflow = await resources(os.environ.get("RETRIEVAL_MODE", "dense"))
    try:
        judge = DecisionJudge(config)
        result = await evaluate_case(workflow, case, judge)
        eval_capture(result, corpus=workflow.pinned_version)
        assert all(value for key, value in result["checks"].items() if key != "missing_price_ids"), result[
            "checks"
        ]
        assert_test(to_test_case(case, result["answer"]), make_metrics(judge))
    except Exception as exc:
        if "result" not in locals():
            eval_capture(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "passed": False,
                    "error": type(exc).__name__,
                    "error_category": "infrastructure_or_protocol",
                },
                corpus=workflow.pinned_version,
            )
        raise
    finally:
        await db.close()
