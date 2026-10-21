import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from placidway import auth
from placidway.config import settings
from placidway.db import Database
from placidway.ingestion import refresh
from placidway.llm import Generator
from placidway.retrieval import Embedder, Retriever
from placidway.workflow import Workflow


@asynccontextmanager
async def lifespan(app):
    config = settings()
    db = Database(config)
    await db.open()
    app.state.db = db
    app.state.embedder = Embedder(config.embedding_model)
    app.state.workflow = Workflow(
        db, Retriever(db, app.state.embedder), Generator(config), AsyncPostgresSaver(db.pool)
    )
    app.state.tasks = set()
    app.state.semaphore = asyncio.Semaphore(config.api_concurrency)
    await auth.seed_principals(db)
    await db.execute(
        "UPDATE runs SET status='interrupted',error='Server restarted; please retry',finished_at=now() WHERE status IN ('queued','running')"
    )
    async with db.pool.connection() as connection:
        lock = await (
            await connection.execute("SELECT pg_try_advisory_lock(74190202) AS acquired")
        ).fetchone()
        if lock["acquired"]:
            await connection.execute(
                "UPDATE ingestion_runs SET status='failed',error='Interrupted before restart',finished_at=now() WHERE status='running'"
            )
            await connection.execute("SELECT pg_advisory_unlock(74190202)")
    try:
        yield
    finally:
        for task in app.state.tasks:
            task.cancel()
        await asyncio.gather(*app.state.tasks, return_exceptions=True)
        await db.close()


app = FastAPI(title="PlacidWay assessment API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def protect_browser_mutations(request, call_next):
    if (
        request.method in ("POST", "PUT", "PATCH", "DELETE")
        and request.headers.get("sec-fetch-site") == "cross-site"
    ):
        return Response(status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


class LoginBody(BaseModel):
    secret: str = Field(min_length=1, max_length=512)


class ConversationBody(BaseModel):
    # Legacy language fields are ignored; the resolver follows the user's message.
    pass


class RunBody(ConversationBody):
    message: str = Field(min_length=1, max_length=6000)
    client_message_id: UUID


async def owned_conversation(request, conversation_id, user):
    row = await request.app.state.db.one(
        "SELECT id,title,response_language,created_at,updated_at FROM conversations WHERE id=%s AND principal_id=%s AND purpose='chat'",
        (conversation_id, user["id"]),
    )
    if not row:
        raise HTTPException(404, "Conversation not found")
    return row


async def owned_run(request, run_id, user):
    row = await request.app.state.db.one(
        "SELECT r.* FROM runs r JOIN conversations c ON c.id=r.conversation_id WHERE r.id=%s AND c.principal_id=%s",
        (run_id, user["id"]),
    )
    if not row:
        raise HTTPException(404, "Run not found")
    return row


def public_run(row):
    return {key: row[key] for key in ("id", "conversation_id", "status", "created_at", "error")} | {
        "events_url": f"/api/runs/{row['id']}/events",
        "user_message_id": row["client_message_id"],
        "input": row["input"],
        "response_language": row["response_language"],
        "retryable": row["status"] in ("failed", "interrupted"),
    }


@app.post("/api/auth/login")
async def sign_in(body: LoginBody, request: Request, response: Response):
    return await auth.login(request, response, body.secret)


@app.get("/api/auth/me")
async def me(user=Depends(auth.current_user)):
    return user


@app.post("/api/auth/logout", status_code=204)
async def sign_out(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE)
    if token:
        await request.app.state.db.execute(
            "DELETE FROM sessions WHERE token_hash=%s", (auth.token_hash(token),)
        )
    response.delete_cookie(auth.COOKIE, path="/api")


@app.get("/api/conversations")
async def conversations(request: Request, user=Depends(auth.current_user)):
    rows = await request.app.state.db.all(
        "SELECT id,title,response_language,created_at,updated_at FROM conversations WHERE principal_id=%s AND purpose='chat' ORDER BY updated_at DESC",
        (user["id"],),
    )
    return {"items": rows}


@app.post("/api/conversations", status_code=201)
async def create_conversation(body: ConversationBody, request: Request, user=Depends(auth.current_user)):
    key = uuid4()
    await request.app.state.db.execute(
        "INSERT INTO conversations(id,principal_id,response_language) VALUES(%s,%s,%s)",
        (key, user["id"], "auto"),
    )
    return await owned_conversation(request, key, user)


@app.get("/api/conversations/{conversation_id}/messages")
async def messages(conversation_id: UUID, request: Request, user=Depends(auth.current_user)):
    await owned_conversation(request, conversation_id, user)
    items = await request.app.state.workflow.history(conversation_id)
    active = await request.app.state.db.one(
        "SELECT * FROM runs WHERE conversation_id=%s AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
        (conversation_id,),
    )
    latest = await request.app.state.db.one(
        "SELECT * FROM runs WHERE conversation_id=%s ORDER BY created_at DESC LIMIT 1", (conversation_id,)
    )
    return {
        "items": items,
        "active_run": public_run(active) if active else None,
        "latest_run": public_run(latest) if latest else None,
    }


async def event(db, run_id, name, data):
    await db.execute(
        "INSERT INTO run_events(run_id,event,data) VALUES(%s,%s,%s)", (run_id, name, Jsonb(data))
    )


def background(app, coroutine):
    task = asyncio.create_task(coroutine)
    app.state.tasks.add(task)
    task.add_done_callback(app.state.tasks.discard)
    return task


async def execute_run(application, row):
    db = application.state.db
    key = row["id"]
    async with application.state.semaphore:
        await db.execute("UPDATE runs SET status='running' WHERE id=%s", (key,))
        await event(
            db, key, "run.started", {"run_id": str(key), "conversation_id": str(row["conversation_id"])}
        )

        async def stage(data):
            await event(db, key, "stage", data)

        try:
            answer, message = await application.state.workflow.run(
                str(row["conversation_id"]),
                row["input"],
                row["response_language"],
                str(row["client_message_id"]),
                str(key),
                stage,
            )
            await event(
                db,
                key,
                "answer.start",
                {"message_id": message["id"], "response_language": answer["response_language"]},
            )
            content = answer["content"]
            for start in range(0, len(content), 100):
                await event(db, key, "answer.delta", {"text": content[start : start + 100]})
            async with db.pool.connection() as connection:
                async with connection.transaction():
                    await connection.execute(
                        "UPDATE runs SET status='completed',finished_at=now(),result=%s,response_language=%s WHERE id=%s",
                        (Jsonb(answer), answer["response_language"], key),
                    )
                    await connection.execute(
                        "UPDATE conversations SET response_language=%s WHERE id=%s",
                        (answer["response_language"], row["conversation_id"]),
                    )
                    await connection.execute(
                        "INSERT INTO run_events(run_id,event,data) VALUES(%s,'answer.complete',%s)",
                        (key, Jsonb({"message": message})),
                    )
        except asyncio.CancelledError:
            await db.execute(
                "UPDATE runs SET status='interrupted',finished_at=now(),error='Server stopped; please retry' WHERE id=%s",
                (key,),
            )
            raise
        except Exception as exc:
            name = type(exc).__name__
            await db.execute(
                "UPDATE runs SET status='failed',finished_at=now(),error=%s WHERE id=%s", (name, key)
            )
            await event(
                db,
                key,
                "run.error",
                {"message": "The answer could not be completed. Please retry.", "retryable": True},
            )


@app.post("/api/conversations/{conversation_id}/runs", status_code=202)
async def start_run(conversation_id: UUID, body: RunBody, request: Request, user=Depends(auth.current_user)):
    db = request.app.state.db
    await owned_conversation(request, conversation_id, user)
    existing = await db.one(
        "SELECT * FROM runs WHERE conversation_id=%s AND client_message_id=%s",
        (conversation_id, body.client_message_id),
    )
    if existing:
        return public_run(existing)
    if not body.message.strip():
        raise HTTPException(422, "Message cannot be blank")
    if not db.config.generation_ready or not await db.active_version():
        raise HTTPException(
            503, "The chatbot is initializing. Please check the knowledge status and try again"
        )
    count = await db.one(
        "SELECT count(*) AS n FROM runs r JOIN conversations c ON c.id=r.conversation_id WHERE c.principal_id=%s AND r.status IN ('queued','running')",
        (user["id"],),
    )
    if count["n"] >= 3:
        raise HTTPException(429, "Please wait for your current answers to finish")
    key = uuid4()
    try:
        await db.execute(
            "INSERT INTO runs(id,conversation_id,client_message_id,input,response_language) VALUES(%s,%s,%s,%s,%s)",
            (key, conversation_id, body.client_message_id, body.message.strip(), "auto"),
        )
    except UniqueViolation:
        existing = await db.one(
            "SELECT * FROM runs WHERE conversation_id=%s AND client_message_id=%s",
            (conversation_id, body.client_message_id),
        )
        if existing:
            return public_run(existing)
        raise HTTPException(409, "This conversation is already processing an answer") from None
    await db.execute(
        "UPDATE conversations SET title=CASE WHEN title='New conversation' THEN %s ELSE title END,updated_at=now() WHERE id=%s",
        (body.message.strip()[:70], conversation_id),
    )
    row = await db.one("SELECT * FROM runs WHERE id=%s", (key,))
    background(request.app, execute_run(request.app, row))
    return public_run(row)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: UUID, request: Request, user=Depends(auth.current_user)):
    return public_run(await owned_run(request, run_id, user))


@app.get("/api/runs/{run_id}/events")
async def events(run_id: UUID, request: Request, after: int = 0, user=Depends(auth.current_user)):
    await owned_run(request, run_id, user)
    try:
        cursor = max(after, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        raise HTTPException(400, "Invalid stream event ID") from None

    async def stream():
        nonlocal cursor
        heartbeat = asyncio.get_running_loop().time()
        while not await request.is_disconnected():
            # Revalidate expiry/revocation while a stream is open.
            try:
                await auth.current_user(request)
            except HTTPException:
                yield 'event: run.error\ndata: {"message":"Session expired. Please sign in again.","retryable":false}\n\n'
                return
            rows = await request.app.state.db.all(
                "SELECT id,event,data FROM run_events WHERE run_id=%s AND id>%s ORDER BY id", (run_id, cursor)
            )
            for row in rows:
                cursor = row["id"]
                yield f"id: {cursor}\nevent: {row['event']}\ndata: {json.dumps(row['data'], ensure_ascii=False)}\n\n"
                if row["event"] in ("answer.complete", "run.error"):
                    return
            run = await request.app.state.db.one("SELECT status FROM runs WHERE id=%s", (run_id,))
            if not rows and run["status"] in ("completed", "failed", "interrupted"):
                return
            if asyncio.get_running_loop().time() - heartbeat > 15:
                yield ": heartbeat\n\n"
                heartbeat = asyncio.get_running_loop().time()
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.get("/api/knowledge/status")
async def knowledge_status(request: Request, user=Depends(auth.current_user)):
    db = request.app.state.db
    version = await db.active_version()
    counts = {"source_count": 0, "chunk_count": 0, "price_count": 0}
    updated = None
    if version:
        for table, key in (
            ("source_versions", "source_count"),
            ("chunks", "chunk_count"),
            ("price_facts", "price_count"),
        ):
            row = await db.one(f"SELECT count(*) AS n FROM {table} WHERE version=%s", (version,))
            counts[key] = row["n"]
        row = await db.one(
            "SELECT max(fetched_at) AS updated FROM source_versions WHERE version=%s", (version,)
        )
        updated = row["updated"]
    last = await db.one("SELECT status,error FROM ingestion_runs ORDER BY started_at DESC LIMIT 1")
    return {
        "ready": bool(version),
        "version": version,
        "updated_at": updated,
        **counts,
        "refresh_running": bool(last and last["status"] == "running"),
        "last_error": last["error"] if last else None,
    }


async def safe_refresh(application):
    with contextlib.suppress(Exception):
        await refresh(application.state.db, application.state.embedder)


@app.post("/api/admin/knowledge/refresh", status_code=202)
async def refresh_knowledge(request: Request, user=Depends(auth.current_user)):
    if user["role"] != "owner":
        raise HTTPException(403, "Only the owner can refresh content")
    running = await request.app.state.db.one("SELECT id FROM ingestion_runs WHERE status='running' LIMIT 1")
    if running:
        return {"status": "already_running"}
    background(request.app, safe_refresh(request.app))
    return {"status": "started"}


@app.get("/api/health/live")
async def live():
    return {"status": "ok"}


@app.get("/api/health/ready")
async def ready(request: Request, response: Response):
    config = request.app.state.db.config
    try:
        async with asyncio.timeout(3):
            version = await request.app.state.db.active_version()
    except Exception:
        response.status_code = 503
        return {
            "ready": False,
            "database_ready": False,
            "knowledge_ready": False,
            "generation_configured": config.generation_ready,
        }
    ok = bool(version and config.generation_ready)
    if not ok:
        response.status_code = 503
    return {
        "ready": ok,
        "database_ready": True,
        "knowledge_ready": bool(version),
        "generation_configured": config.generation_ready,
    }
