"""Publication rollback and reuse against real PostgreSQL, without crawling."""

import os
from decimal import Decimal
from uuid import uuid4

import pytest

from placidway.config import settings
from placidway.db import Database
from placidway.ingestion import publish_pages, refresh
from placidway.models import Evidence, ExtractedPage, PriceFact
from placidway.sources import SOURCE_URLS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="Requires migrated PostgreSQL"),
]


class FailingEmbedder:
    def chunks(self, *args):
        raise RuntimeError("Intentional embedding failure")


async def test_unchanged_reuses_embeddings_and_failure_rolls_back(monkeypatch):
    db = Database(settings())
    await db.open()
    try:
        before = await db.active_version()
        sources = await db.all("SELECT * FROM source_versions WHERE version=%s ORDER BY source_id", (before,))
        sections = await db.all("SELECT source_id,payload FROM evidence WHERE version=%s", (before,))
        prices = await db.all("SELECT payload FROM price_facts WHERE version=%s", (before,))
        pages = [
            ExtractedPage(
                source_id=row["source_id"],
                url=row["url"],
                title=row["title"],
                content_hash=row["content_hash"],
                extractor_version=row["extractor_version"],
                sections=[item["payload"] for item in sections if item["source_id"] == row["source_id"]],
                prices=[
                    item["payload"] for item in prices if item["payload"]["source_id"] == row["source_id"]
                ],
            )
            for row in sources
        ]
        assert len(pages) == 7
        assert await publish_pages(db, FailingEmbedder(), pages) == (before, 0)
        count_before = await db.one("SELECT count(*) AS n FROM source_versions")
        pages[0].content_hash = "test-change-" + str(uuid4())
        with pytest.raises(RuntimeError, match="Intentional"):
            await publish_pages(db, FailingEmbedder(), pages)
        assert await db.active_version() == before
        assert await db.one("SELECT count(*) AS n FROM source_versions") == count_before
        # A held crawl lock prevents a second worker from fetching anything.
        async with db.pool.connection() as connection:
            await connection.execute("SELECT pg_advisory_lock(74190202)")
            try:
                assert await refresh(db, FailingEmbedder()) == {"status": "already_running"}
            finally:
                await connection.execute("SELECT pg_advisory_unlock(74190202)")
    finally:
        await db.close()


async def test_changed_publication_replaces_prices_and_keeps_pinned_old_corpus(isolated_database):
    import numpy as np

    db = isolated_database

    class CountingEmbedder:
        encoded = []

        def chunks(self, sections):
            return [(section.id + ":chunk", section.id, section.text) for section in sections]

        def encode(self, texts):
            self.encoded.extend(texts)
            return np.ones((len(texts), 384), dtype=np.float32)

    embedder = CountingEmbedder()
    pages = []
    for source, url in SOURCE_URLS.items():
        section = Evidence(
            id=f"s{source}:old",
            source_id=source,
            url=url,
            title="Source",
            heading="Prices",
            text="USD 100 outpatient",
        )
        prices = (
            [
                PriceFact(
                    id="p2:1",
                    source_id=2,
                    evidence_id=section.id,
                    treatment="Adenocarcinoma Cancer",
                    amount=Decimal("100"),
                )
            ]
            if source == 2
            else []
        )
        pages.append(
            ExtractedPage(
                source_id=source,
                url=url,
                title="Source",
                content_hash=f"old-{source}",
                sections=[section],
                prices=prices,
            )
        )
    old, changed = await publish_pages(db, embedder, pages)
    assert changed == 7 and len(embedder.encoded) == 7
    pages[1] = pages[1].model_copy(deep=True)
    pages[1].content_hash = "new-source-2"
    pages[1].sections[0].text = "USD 150 outpatient"
    pages[1].sections[0].id = "s2:new"
    pages[1].prices[0].amount = Decimal("150")
    pages[1].prices[0].evidence_id = "s2:new"
    embedder.encoded = []
    new, changed = await publish_pages(db, embedder, pages)
    assert changed == 1 and len(embedder.encoded) == 1 and await db.active_version() == new
    active = await db.one("SELECT payload FROM price_facts WHERE version=%s", (new,))
    pinned = await db.one("SELECT payload FROM price_facts WHERE version=%s", (old,))
    assert active["payload"]["amount"] == "150" and pinned["payload"]["amount"] == "100"
    assert (await db.one("SELECT count(*) AS n FROM source_versions WHERE version=%s", (new,)))["n"] == 7
    assert (await db.one("SELECT count(*) AS n FROM chunks WHERE version=%s", (new,)))["n"] == 7
    pages[0].extractor_version = "test-next-revision"
    embedder.encoded = []
    revision, changed = await publish_pages(db, embedder, pages)
    assert changed == 1 and len(embedder.encoded) == 1 and revision != new
