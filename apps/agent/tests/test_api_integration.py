"""Uses a migrated PostgreSQL database and real checkpoints; no provider calls."""

import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from test_workflow import FakeGenerator, PriceRetriever, cost, search

from placidway.api import app, lifespan
from placidway.workflow import Workflow

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="Requires migrated PostgreSQL"),
]


async def test_auth_isolation_idempotent_stream_replay_and_checkpoint_resume():
    async with lifespan(app):
        db = app.state.db
        saver = AsyncPostgresSaver(db.pool)
        generator = FakeGenerator([search(), cost()])
        app.state.workflow = Workflow(db, PriceRetriever(), generator, saver)
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test") as owner,
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            assert (await owner.get("/api/conversations")).status_code == 401
            assert (
                await owner.post("/api/auth/login", json={"secret": "invalid-test-secret"})
            ).status_code == 401
            assert (
                await owner.post("/api/auth/login", json={"secret": db.config.auth_owner_secret})
            ).status_code == 200
            assert (
                await client.post("/api/auth/login", json={"secret": db.config.auth_client_secret})
            ).status_code == 200
            assert (await client.post("/api/admin/knowledge/refresh")).status_code == 403
            assert (
                await owner.post("/api/conversations", json={}, headers={"Sec-Fetch-Site": "cross-site"})
            ).status_code == 403
            created = (await owner.post("/api/conversations", json={"response_language": "ar"})).json()
            conversation = created["id"]
            assert created["response_language"] == "auto"
            try:
                assert (await client.get(f"/api/conversations/{conversation}/messages")).status_code == 404
                payload = {
                    "message": "Outpatient adenocarcinoma price?",
                    "client_message_id": str(uuid4()),
                    "response_language": "ar",
                }
                endpoint = f"/api/conversations/{conversation}/runs"
                response = await owner.post(endpoint, json=payload)
                assert response.status_code == 202
                run = response.json()
                assert (await owner.post(endpoint, json=payload)).json()["id"] == run["id"]
                for _ in range(100):
                    status = (await owner.get(f"/api/runs/{run['id']}")).json()["status"]
                    if status == "completed":
                        break
                    await asyncio.sleep(0.02)
                assert status == "completed"
                persisted = await db.one("SELECT response_language FROM runs WHERE id=%s", (run["id"],))
                assert persisted["response_language"] == "en"
                persisted = await db.one(
                    "SELECT response_language FROM conversations WHERE id=%s", (conversation,)
                )
                assert persisted["response_language"] == "en"
                assert (await client.get(run["events_url"])).status_code == 404
                stream = (await owner.get(run["events_url"])).text
                assert "event: stage" in stream and "event: answer.complete" in stream
                assert stream.index("event: answer.delta") < stream.index('"stage": "review"')
                ids = [int(line[4:]) for line in stream.splitlines() if line.startswith("id: ")]
                replay = (await owner.get(run["events_url"], headers={"Last-Event-ID": str(ids[1])})).text
                assert all(int(line[4:]) > ids[1] for line in replay.splitlines() if line.startswith("id: "))
                assert "event: answer.complete" in replay
                # New workflow/checkpointer instance reads the persisted transcript.
                restored = Workflow(db, PriceRetriever(), generator, AsyncPostgresSaver(db.pool))
                history = await restored.history(conversation)
                assert len(history) == 2 and "USD 18,995" in history[-1]["content"]
                assert history[-1]["response_language"] == "en"
                assert "diagnostics" not in json.dumps(history)
                # A second browser authenticated to the same account restores the same history.
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as second_browser:
                    assert (
                        await second_browser.post(
                            "/api/auth/login", json={"secret": db.config.auth_owner_secret}
                        )
                    ).status_code == 200
                    assert (await second_browser.get(f"/api/conversations/{conversation}/messages")).json()[
                        "items"
                    ] == history
                    from placidway.auth import COOKIE, token_hash

                    token = second_browser.cookies.get(COOKIE)
                    await db.execute(
                        "UPDATE sessions SET expires_at=now()-interval '1 second' WHERE token_hash=%s",
                        (token_hash(token),),
                    )
                    assert (await second_browser.get("/api/auth/me")).status_code == 401
                failed_id = uuid4()
                failed_message = uuid4()
                await db.execute(
                    "INSERT INTO runs(id,conversation_id,client_message_id,input,response_language,status,error) VALUES(%s,%s,%s,'Retry this question','en','interrupted','Server restarted')",
                    (failed_id, conversation, failed_message),
                )
                restored_response = (await owner.get(f"/api/conversations/{conversation}/messages")).json()
                assert restored_response["active_run"] is None
                assert restored_response["latest_run"]["user_message_id"] == str(failed_message)
                assert (
                    restored_response["latest_run"]["retryable"]
                    and restored_response["latest_run"]["status"] == "interrupted"
                )
                assert (await owner.post("/api/auth/logout")).status_code == 204
                assert (await owner.get("/api/conversations")).status_code == 401
            finally:
                await db.execute(
                    "DELETE FROM run_events WHERE run_id IN (SELECT id FROM runs WHERE conversation_id=%s)",
                    (conversation,),
                )
                await db.execute("DELETE FROM runs WHERE conversation_id=%s", (conversation,))
                await db.execute("DELETE FROM conversations WHERE id=%s", (conversation,))
                await saver.adelete_thread(f"agent-v2:{conversation}")


async def test_readiness_reports_database_failure_as_unavailable(monkeypatch):
    class Unavailable:
        config = type("Config", (), {"generation_ready": True})()

        async def active_version(self):
            raise ConnectionError("internal database details")

    monkeypatch.setattr(app.state, "db", Unavailable(), raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as browser:
        response = await browser.get("/api/health/ready")
        assert response.status_code == 503 and not response.json()["database_ready"]
        assert "internal database details" not in response.text
