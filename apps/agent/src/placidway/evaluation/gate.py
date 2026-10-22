"""Run every required native DeepEval suite; never report a skipped gate as PASS."""

import asyncio
import fcntl
import json
import os
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from placidway.config import settings
from placidway.db import Database
from placidway.evaluation.provenance import provenance, require_calibration
from placidway.evaluation.runner import load_dataset, verify_corpus


def run_gate():
    if os.environ.get("EVAL_LIMIT"):
        raise SystemExit("EVAL_LIMIT must be unset for release verification")
    config = settings()
    require_calibration(config)
    dataset = load_dataset()
    folder = config.reports_dir.resolve() / "gates" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True)
    suites = [
        ("single", "tests/evals/test_single_turn.py", len(dataset["cases"])),
        ("conversation", "tests/evals/test_conversation.py", len(dataset["conversations"])),
        ("language", "tests/evals/test_automatic_language.py", 11),
        ("refactor", "tests/evals/test_refactor.py", 8),
    ]

    async def audited_corpus():
        db = Database(config)
        await db.open()
        try:
            return await verify_corpus(db)
        finally:
            await db.close()

    corpus = asyncio.run(audited_corpus())
    report = {"status": "INCOMPLETE", "provenance": provenance(config, corpus), "suites": []}
    report_path = folder / "gate.json"
    report_path.write_text(json.dumps(report, indent=2))
    for name, path, expected in suites:
        junit = folder / f"{name}.xml"
        env = {
            **os.environ,
            "RUN_LIVE_EVALS": "1",
            "EVAL_SPLIT": "all",
            "RETRIEVAL_MODE": "hybrid",
            "PYTEST_ADDOPTS": f"--junitxml={junit} -q",
            "DEEPEVAL_TELEMETRY_OPT_OUT": "YES",
        }
        # Use the same environment as this module, with the native DeepEval CLI.
        # DeepEval's distribution exposes its CLI as a console script, not __main__.
        command = [str(Path(sys.executable).parent / "deepeval"), "test", "run", path, "--num-processes", "2"]
        print(f"Running {name}: {path}", flush=True)
        with (folder / f"{name}.log").open("w") as log:
            process = subprocess.Popen(
                command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
            try:
                returncode = process.wait()
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
                raise
        row = {"name": name, "expected": expected, "exit_code": returncode, "status": "INCOMPLETE"}
        if junit.exists():
            root = ET.parse(junit).getroot()
            elements = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
            counts = {
                key: sum(int(e.attrib.get(key, 0)) for e in elements)
                for key in ("tests", "failures", "errors", "skipped")
            }
            row.update(counts)
            row["status"] = (
                "PASS"
                if returncode == 0
                and counts["tests"] == expected
                and not any(counts[k] for k in ("failures", "errors", "skipped"))
                else "FAIL"
            )
        report["suites"].append(row)
        report_path.write_text(json.dumps(report, indent=2))
        print(f"{name}: {row['status']} (details in {folder / (name + '.log')})", flush=True)
    final_provenance = provenance(config, corpus)
    unchanged = all(
        final_provenance[key] == report["provenance"][key]
        for key in ("code_version", "prompt_version", "rubric_version")
    )
    report["candidate_unchanged"] = unchanged
    report["status"] = (
        "PASS" if unchanged and all(s["status"] == "PASS" for s in report["suites"]) else "FAIL"
    )
    report_path.write_text(json.dumps(report, indent=2))
    print(f"DeepEval gate: {report['status']}; {report_path}", flush=True)
    raise SystemExit(0 if report["status"] == "PASS" else 1)


def main():
    folder = settings().reports_dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".native-evaluation.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another DeepEval gate is running; suites must execute sequentially.") from None

        def interrupted(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupted)
        run_gate()


if __name__ == "__main__":
    main()
