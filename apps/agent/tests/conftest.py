"""Create disposable databases for publication tests; never activate synthetic data in the app DB."""

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from placidway.config import settings
from placidway.db import Database, migrate


@pytest.fixture
async def isolated_database():
    if os.environ.get("RUN_DB_TESTS") != "1":
        pytest.skip("Requires PostgreSQL database-creation access")
    config = settings()
    name = "placidway_contract_test_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(config.database_url, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        isolated = config.model_copy(update={"database_url": make_conninfo(config.database_url, dbname=name)})
        db = Database(isolated)
        try:
            await migrate(isolated)
            await db.open()
            yield db
        finally:
            await db.close()
            await admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
