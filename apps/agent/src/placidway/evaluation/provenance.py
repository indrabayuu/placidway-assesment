"""Local evaluation fingerprints, fixed calibration gate, and bounded improvement ledger."""

import fcntl
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from placidway import prompts
from placidway.evaluation.judge import CONVERSATION_QUESTIONS, QUESTIONS
from placidway.ingestion import EXTRACTOR_VERSION, INDEX_VERSION


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rubric_version():
    return fingerprint({"single_turn": QUESTIONS, "conversation": CONVERSATION_QUESTIONS})


def provenance(config, corpus):
    return {
        "corpus": corpus,
        "extractor_version": EXTRACTOR_VERSION,
        "index_version": INDEX_VERSION,
        "prompt_version": fingerprint(
            {name: prompts.load_system_prompt(name.lower()) for name in ("MAIN", "ADVISOR", "REVIEW")}
        ),
        "rubric_version": rubric_version(),
        "generation_model": config.openai_model,
        "reasoning_effort": {
            "main": config.main_reasoning_effort,
            "advisor": config.helper_reasoning_effort,
            "review": config.helper_reasoning_effort,
        },
        "judge_model": config.openrouter_model,
        "judge_threshold": 0.8,
        "embedding_model": config.embedding_model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_version": fingerprint(
            {
                str(path.relative_to(Path(__file__).resolve().parents[3])): path.read_text()
                for path in sorted(Path(__file__).resolve().parents[3].joinpath("src").rglob("*.py"))
            }
        ),
    }


def require_calibration(config):
    path = config.reports_dir / "judge-calibration.json"
    if not path.exists():
        raise RuntimeError("Run calibrate-judge before provider-backed evaluation")
    audit = json.loads(path.read_text())
    if (
        audit.get("model") != config.openrouter_model
        or audit.get("rubric_version") != rubric_version()
        or audit.get("threshold") != 0.8
    ):
        raise RuntimeError("Judge model/rubric changed; calibrate-judge before evaluation")
    if audit.get("agreement_rate", 0) < 0.8:
        raise RuntimeError("Judge calibration agreement is below the frozen acceptance gate")


def record_round(config, number, failure, change):
    if not 1 <= number <= config.eval_max_improvement_rounds:
        raise ValueError("Improvement round is outside the configured maximum (at most 10)")
    if not failure.strip() or not change.strip():
        raise ValueError("Improvement rounds require an observed failure and targeted change")
    path = config.reports_dir / "improvement-rounds.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _record_round(path, number, failure, change)


def _record_round(path, number, failure, change):
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        seed = Path(__file__).resolve().parents[3] / "tests/evals/improvement-history.json"
        rows = json.loads(seed.read_text()) if seed.exists() else []
    if any(row["round"] == number for row in rows):
        raise ValueError("This improvement round is already recorded; verification does not consume a round")
    rows.append({"round": number, "failure": failure, "change": change, "status": "running"})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n")
    return path


def finish_round(path, number, folder, results):
    rows = json.loads(path.read_text())
    row = next(row for row in rows if row["round"] == number)
    row.update(
        status="completed",
        report=str(folder),
        passed=sum(item["passed"] for item in results),
        total=len(results),
    )
    path.write_text(json.dumps(rows, indent=2) + "\n")
