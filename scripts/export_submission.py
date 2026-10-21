"""Export synthetic benchmark answers and summaries, excluding private diagnostics."""

import argparse
import hashlib
import json
import shutil
import statistics
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--single", required=True, help="Report folder relative to repository root")
parser.add_argument("--conversation", required=True)
args = parser.parse_args()
single = json.loads((root / args.single / "results.json").read_text())
conversation = json.loads((root / args.conversation / "results.json").read_text())
out = root / "docs/results"
out.mkdir(parents=True, exist_ok=True)
compact = []
for row in single["results"]:
    answer = row.get("answer", {})
    compact.append(
        {key: row[key] for key in ("id", "question", "passed", "checks", "scores", "expected") if key in row}
        | {
            "content": answer.get("content", ""),
            "outcome": answer.get("outcome"),
            "citations": answer.get("citations", []),
            "used_price_ids": answer.get("diagnostics", {}).get("used_price_ids", []),
            "seconds": answer.get("diagnostics", {}).get("seconds"),
            "calls": answer.get("diagnostics", {}).get("calls", []),
            **({"error": row["error"]} if "error" in row else {}),
        }
    )
code_files = sorted((root / "apps/agent/src").rglob("*.py"))
fingerprint = hashlib.sha256(
    b"".join(str(path.relative_to(root)).encode() + path.read_bytes() for path in code_files)
).hexdigest()
(out / "single-turn.json").write_text(
    json.dumps(
        {"metadata": single["metadata"], "code_sha256": fingerprint, "results": compact},
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
shareable_conversation = json.loads(json.dumps(conversation))
for row in shareable_conversation["results"]:
    for answer in row.get("answers", []):
        diagnostics = answer.pop("diagnostics", {})
        answer["seconds"] = diagnostics.get("seconds")
        answer["calls"] = diagnostics.get("calls", [])
        answer["validation_issues"] = diagnostics.get("validation_issues", [])
(out / "conversations.json").write_text(
    json.dumps(shareable_conversation, ensure_ascii=False, indent=2) + "\n"
)
for source, target in [
    (root / "reports/retrieval/comparison.json", "retrieval.json"),
    (root / "reports/judge-calibration.json", "judge-calibration.json"),
    (root / "apps/agent/tests/evals/corpus-manifest.json", "corpus-manifest.json"),
]:
    shutil.copyfile(source, out / target)
manifest = json.loads((out / "corpus-manifest.json").read_text())["sources"]
for folder in sorted((root / "data/snapshots").glob("*"), reverse=True):
    pages = [json.loads(path.read_text()) for path in sorted(folder.glob("page-*.json"))]
    if len(pages) != 7 or any(
        page["content_hash"] != manifest[str(page["source_id"])]["content_hash"]
        or page.get("extractor_version") != manifest[str(page["source_id"])]["extractor_version"]
        for page in pages
    ):
        continue
    sources = []
    for page in pages:
        sources.append(
            {
                key: page[key]
                for key in (
                    "source_id",
                    "url",
                    "title",
                    "page_type",
                    "fetched_at",
                    "content_hash",
                    "extractor_version",
                    "diagnostics",
                )
            }
            | {"evidence_sections": len(page["sections"]), "price_records": len(page["prices"])}
        )
    (out / "extraction-audit.json").write_text(
        json.dumps(
            {
                "snapshot": folder.name,
                "publication": json.loads((folder / "publication.json").read_text()),
                "sources": sources,
            },
            indent=2,
        )
        + "\n"
    )
    break
else:
    raise RuntimeError("No audited local source snapshot matches the exported manifest")
cases = {row["id"]: row for row in compact}
conversations = {row["id"]: row for row in conversation["results"]}
answers = [
    ("Cost", cases["q01"]["question"], cases["q01"]["content"]),
    ("Comparison", cases["q22"]["question"], cases["q22"]["content"]),
    ("Package inclusions", cases["q06"]["question"], cases["q06"]["content"]),
    (
        "Follow-up",
        "After asking about outpatient adenocarcinoma pricing: And what about recovery time?",
        conversations["c01"]["turns"][-1]["content"],
    ),
    ("Unsupported booking", cases["q24"]["question"], cases["q24"]["content"]),
    ("Out of scope", cases["q25"]["question"], cases["q25"]["content"]),
    ("Medical advice", cases["q26"]["question"], cases["q26"]["content"]),
    ("False price premise", cases["q28"]["question"], cases["q28"]["content"]),
]
lines = [
    "# Eight required assessment answers",
    "",
    "Actual recorded outputs from the final benchmark. The follow-up uses the same persisted conversation. Bracketed examples in the assessment are instantiated with treatments/countries covered by the approved pages.",
    "",
]
for index, (kind, question, answer) in enumerate(answers, 1):
    lines += [f"## {index}. {kind}", "", f"**Question:** {question}", "", answer, ""]
(root / "docs/assessment-answers.md").write_text("\n".join(lines))
durations = [row["seconds"] for row in compact if row["seconds"] is not None]
print(
    json.dumps(
        {
            "single_turn": f"{sum(row['passed'] for row in compact)}/{len(compact)}",
            "conversations": f"{sum(row['passed'] for row in conversation['results'])}/{len(conversation['results'])}",
            "latency_p50_seconds": statistics.median(durations),
            "latency_p95_seconds": sorted(durations)[min(len(durations) - 1, int(0.95 * len(durations)))],
            "delivered_code_sha256": fingerprint,
        }
    )
)
