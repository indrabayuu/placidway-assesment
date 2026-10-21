"""Exercise real providers and optional Compose restart without printing secrets.

From repository root: uv run --project apps/agent python scripts/smoke_compose.py
Add --restart --engine /opt/podman/bin/podman for a persistence restart check.
"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://localhost:8080")
parser.add_argument("--restart", action="store_true")
parser.add_argument("--engine", default="docker")
args = parser.parse_args()
values = dotenv_values(root / ".env")
with httpx.Client(base_url=args.url, timeout=180) as client:
    response = client.post("/api/auth/login", json={"secret": values["AUTH_OWNER_SECRET"]})
    assert response.status_code == 200, "Owner login failed"
    status = client.get("/api/knowledge/status").json()
    assert status["ready"] and status["source_count"] == 7 and status["price_count"] == 59
    conversation = client.post("/api/conversations", json={"response_language": "en"}).json()["id"]
    payload = {
        "message": "How much does outpatient adenocarcinoma treatment in Mexico cost?",
        "client_message_id": str(uuid4()),
    }
    run = client.post(f"/api/conversations/{conversation}/runs", json=payload).json()
    assert client.post(f"/api/conversations/{conversation}/runs", json=payload).json()["id"] == run["id"]
    stream = client.get(run["events_url"]).text
    assert "event: answer.complete" in stream and '"stage": "validate"' in stream
    assert stream.index('"stage": "validate"') < stream.index("event: answer.delta")
    messages = client.get(f"/api/conversations/{conversation}/messages").json()["items"]
    assert len(messages) == 2 and "USD 18,995" in messages[-1]["content"]
    assert messages[-1]["citations"] and "diagnostics" not in json.dumps(messages)
    if args.restart:
        subprocess.run([args.engine, "compose", "restart", "api", "web"], cwd=root, check=True)
        for attempt in range(60):
            try:
                if client.get("/api/health/ready", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        restored = client.get(f"/api/conversations/{conversation}/messages").json()["items"]
        assert restored == messages, "Completed transcript changed after restart"
    print(
        json.dumps(
            {
                "compose_chat": "passed",
                "source_count": 7,
                "price_count": 59,
                "idempotency": "passed",
                "validated_stream": "passed",
                "restart_resume": "passed" if args.restart else "not_run",
            }
        )
    )
