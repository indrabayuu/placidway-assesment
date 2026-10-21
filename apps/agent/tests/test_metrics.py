import os
import subprocess
import sys

import pytest

from placidway.config import Settings
from placidway.evaluation.judge import DecisionJudge
from placidway.evaluation.metrics import retrieval_metrics


def test_recall_counts_both_comparison_sides():
    metrics = retrieval_metrics(
        ["mexico", "noise"], ["mexico", "turkey"], 3, required_groups=[["mexico"], ["turkey"]]
    )
    assert metrics["recall"] == 0.5
    assert metrics["essential_coverage"] == 0.5
    assert metrics["mrr"] == 1


def test_duplicate_chunks_do_not_inflate_gain():
    metrics = retrieval_metrics(["a", "a", "a"], ["a", "b"], 3)
    assert metrics["recall"] == 0.5
    assert metrics["ndcg"] < 1


def test_unknown_has_no_recall_denominator():
    assert retrieval_metrics(["anything"], [], 5)["recall"] is None


def test_config_uses_systemone_once_and_preserves_custom_base():
    cfg = Settings(_env_file=None, openrouter_base_url="")
    assert cfg.judge_url == "https://openrouter.ai/api/v1/systemone"
    cfg = Settings(_env_file=None, openrouter_base_url="https://custom.example/systemone/")
    assert cfg.judge_url == "https://custom.example/systemone"


@pytest.mark.asyncio
async def test_judge_missing_key_fails_explicitly(tmp_path):
    cfg = Settings(_env_file=None, openrouter_api_key="", reports_dir=tmp_path)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        await DecisionJudge(cfg).decide({}, {"good": {"type": "noul", "instructions": "Good?"}})


def test_empty_compose_judge_base_is_normalized_before_deepeval_import():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import placidway; from placidway.evaluation.runner import load_dataset; assert len(load_dataset()['cases']) == 60",
        ],
        env={**os.environ, "OPENROUTER_BASE_URL": ""},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, "Container-style evaluation import failed"
