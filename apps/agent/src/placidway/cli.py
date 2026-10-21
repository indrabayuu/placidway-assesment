import asyncio
import json
from pathlib import Path

import typer

from placidway.config import settings
from placidway.db import Database
from placidway.db import migrate as migrate_database
from placidway.ingestion import extract_page
from placidway.ingestion import refresh as refresh_content
from placidway.retrieval import Embedder
from placidway.sources import SOURCE_URLS

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="PlacidWay content, service, and evaluation commands",
)


@app.command()
def migrate():
    """Apply application schema and LangGraph checkpoint migrations."""
    asyncio.run(migrate_database(settings()))
    typer.echo("Database and checkpointer migrations applied.")


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000):
    """Start the authenticated chat API."""
    import uvicorn

    uvicorn.run("placidway.api:app", host=host, port=port, workers=1)


@app.command()
def refresh():
    """Fetch the seven allowed pages and atomically publish changed content."""

    async def run():
        config = settings()
        db = Database(config)
        await db.open()
        try:
            result = await refresh_content(db, Embedder(config.embedding_model))
            typer.echo(json.dumps(result, default=str))
        finally:
            await db.close()

    asyncio.run(run())


@app.command()
def worker():
    """Initial ingestion and periodic refresh, sharing the database crawl lock."""

    async def run():
        config = settings()
        db = Database(config)
        await db.open()
        embedder = Embedder(config.embedding_model)
        try:
            while True:
                try:
                    result = await refresh_content(db, embedder)
                    typer.echo(json.dumps(result, default=str))
                except Exception as exc:
                    typer.echo(json.dumps({"status": "failed", "error": type(exc).__name__}))
                await asyncio.sleep(config.refresh_interval_seconds)
        finally:
            await db.close()

    asyncio.run(run())


@app.command()
def extract(snapshot_dir: Path):
    """Audit HTML snapshots without network access or database mutations."""
    for key in SOURCE_URLS:
        page = extract_page(key, (snapshot_dir / f"page-{key}.html").read_text())
        typer.echo(
            json.dumps(
                {
                    "source": key,
                    "sections": len(page.sections),
                    "prices": len(page.prices),
                    "diagnostics": page.diagnostics,
                    "hash": page.content_hash,
                }
            )
        )


@app.command()
def provider_check():
    """Minimal live checks without displaying credentials or raw model reasoning."""
    from pydantic import BaseModel

    from placidway.evaluation.judge import DecisionJudge
    from placidway.llm import Generator

    class Check(BaseModel):
        ok: bool

    async def run():
        config = settings()
        result = await Generator(config).call(Check, "Return the requested JSON.", {"ok": True}, [1])
        judgment = await DecisionJudge(config).decide(
            {"source": "Price: USD 18995", "answer": "Price: USD 18995"},
            {"matches": {"type": "noul", "instructions": "Does the answer match the source?"}},
        )
        typer.echo(
            json.dumps({"generation_ok": result.ok, "judge_ok": judgment["answers"]["matches"]["noul"] > 0.5})
        )

    asyncio.run(run())


@app.command()
def evaluate(
    split: str = "dev",
    mode: str = "dense",
    limit: int = 0,
    judge: bool = True,
    improvement_round: int = 0,
    failure: str = "",
    change: str = "",
):
    """Run the source-labelled single-turn benchmark and write local reports."""
    from placidway.evaluation.runner import run_benchmark

    asyncio.run(run_benchmark(split, mode, limit, judge, improvement_round, failure, change))


@app.command()
def compare_retrieval():
    """Compare dense/BM25/hybrid on the same frozen labelled corpus."""
    from placidway.evaluation.runner import compare

    asyncio.run(compare())


@app.command()
def evaluate_conversations(split: str = "dev", mode: str = "hybrid"):
    """Run full multi-turn scenarios with PostgreSQL checkpoint continuity."""
    from placidway.evaluation.runner import run_conversations

    asyncio.run(run_conversations(split, mode))


@app.command()
def calibrate_judge():
    """Audit the frozen decision threshold against separate positive/negative fixtures."""
    from placidway.evaluation.calibration import calibrate

    asyncio.run(calibrate())
