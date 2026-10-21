import json
import os
from datetime import datetime, timezone

import pytest

from placidway.config import settings
from placidway.evaluation.provenance import provenance, require_calibration
from placidway.evaluation.runner import load_dataset, report


@pytest.fixture(scope="session", autouse=True)
def calibrated_live_judge():
    if os.environ.get("RUN_LIVE_EVALS") == "1":
        require_calibration(settings())


@pytest.fixture(scope="session")
def eval_capture():
    config = settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folders = {}

    def capture(row, kind="single", corpus=None):
        folder = config.reports_dir / "evals" / f"{stamp}-native-{kind}-{os.environ.get('EVAL_SPLIT', 'dev')}"
        (folder / "cases").mkdir(parents=True, exist_ok=True)
        (folder / "cases" / (row["id"] + ".json")).write_text(json.dumps(row, ensure_ascii=False, indent=2))
        folders[kind] = (folder, corpus)

    yield capture
    for kind, (folder, corpus) in folders.items():
        results = [json.loads(path.read_text()) for path in sorted((folder / "cases").glob("*.json"))]
        metadata = {
            **provenance(config, corpus),
            "split": os.environ.get("EVAL_SPLIT", "dev"),
            "mode": os.environ.get("RETRIEVAL_MODE", config.retrieval_mode),
            "run_type": "verification",
            "native_deepeval": True,
            "dataset_revision": load_dataset()["dataset_revision"],
        }
        if kind == "single":
            report(folder, results, metadata)
        else:
            (folder / "results.json").write_text(
                json.dumps({**metadata, "results": results}, ensure_ascii=False, indent=2)
            )
        print(f"Local native evaluation report: {folder}", flush=True)
