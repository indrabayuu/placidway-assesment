"""Small explicit SQL repository; LangGraph owns its separate checkpoint tables."""

from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from placidway.config import Settings


async def configure(connection):
    await register_vector_async(connection)


class Database:
    def __init__(self, config: Settings):
        self.config = config
        self.pool = AsyncConnectionPool(
            config.database_url,
            min_size=1,
            max_size=8,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
            configure=configure,
        )

    async def open(self):
        await self.pool.open()
        await self.pool.wait(timeout=30)

    async def close(self):
        await self.pool.close()

    async def one(self, query: str, params=()):
        async with self.pool.connection() as connection:
            cursor = await connection.execute(query, params)
            return await cursor.fetchone()

    async def all(self, query: str, params=()):
        async with self.pool.connection() as connection:
            cursor = await connection.execute(query, params)
            return await cursor.fetchall()

    async def execute(self, query: str, params=()):
        async with self.pool.connection() as connection:
            cursor = await connection.execute(query, params)
            return cursor.rowcount

    async def active_version(self) -> str | None:
        row = await self.one("SELECT value FROM settings WHERE key='active_version'")
        return row["value"] if row else None


async def migrate(config: Settings):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with await psycopg.AsyncConnection.connect(
        config.database_url, autocommit=True, row_factory=dict_row, prepare_threshold=0
    ) as connection:
        # Session advisory lock serializes schema/checkpointer initialization.
        await connection.execute("SELECT pg_advisory_lock(74190201)")
        try:
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                await connection.execute(migration.read_text(), prepare=False)
            await AsyncPostgresSaver(connection).setup()
        finally:
            await connection.execute("SELECT pg_advisory_unlock(74190201)")
