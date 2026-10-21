"""Allowlisted fetching, source-specific extraction, and atomic corpus publication."""

import asyncio
import hashlib
import json
import re
import time
import urllib.robotparser
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from psycopg.types.json import Jsonb

from placidway.db import Database
from placidway.models import Evidence, EvidenceTable, ExtractedPage, PriceFact
from placidway.sources import PAGE_TYPES, SOURCE_URLS, TREATMENTS, USER_AGENT

EXTRACTOR_VERSION = "4"
INDEX_VERSION = "minilm-220-table-rows-v2"


class ExtractionError(ValueError):
    """A safe, application-authored source defect, containing no HTML or credentials."""


MONEY = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)(?:\s*[-–]\s*\$?\s*(\d[\d,]*(?:\.\d+)?))?")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def table_rows(table: Tag) -> list[list[str]]:
    return [
        [clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
        for row in table.find_all("tr")
        if row.find_all(["th", "td"])
    ]


def parse_money(text: str):
    match = MONEY.search(text)
    if not match:
        return None
    low = Decimal(match[1].replace(",", ""))
    high = Decimal(match[2].replace(",", "")) if match[2] else None
    return low, high


def extract_page(source_id: int, html: str) -> ExtractedPage:
    soup = BeautifulSoup(html, "html.parser")
    title = clean(soup.h1.get_text(" ", strip=True)) if soup.h1 else ""
    root = soup.select_one("main.article-dt-desc .pack-inner-content")
    if not root or not title:
        raise ExtractionError(f"Source {source_id}: principal content/title selector missing")
    for node in root.select("script,style,noscript,form,button,iframe,.read-more-button"):
        node.decompose()
    # The package templates embed recommendation links in a malformed <p><h3> wrapper.
    # Remove that bounded card, without discarding the surrounding clinical article.
    for label in root.find_all(["h2", "h3", "h4"]):
        if re.fullmatch(r"Related (?:Experiences|Articles|Packages)\s*:?", clean(label.get_text()), re.I):
            wrapper = label.parent
            if wrapper.name == "p":
                wrapper.decompose()
            else:
                for sibling in list(label.next_siblings):
                    if isinstance(sibling, Tag) and sibling.name in ("h1", "h2", "h3", "h4"):
                        break
                    sibling.extract()
                label.decompose()
    sections: list[Evidence] = []
    heading = title
    lines: list[str] = []
    headings = {1: title}
    tables: list[EvidenceTable] = []

    def flush():
        nonlocal lines, tables
        text = "\n".join(dict.fromkeys(line for line in lines if line))
        lines = []
        if not text:
            return
        sid = f"s{source_id}:" + digest(heading + "\n" + text)[:16]
        kind = (
            "testimonial"
            if re.search(r"testimonial|success stor|survivor|remission|journey", heading, re.I)
            else "section"
        )
        if source_id == 5 and heading != title and "about" not in heading.lower():
            kind = "video_listing"
        sections.append(
            Evidence(
                id=sid,
                source_id=source_id,
                url=SOURCE_URLS[source_id],
                title=title,
                heading=heading,
                text=text,
                kind=kind,
                treatment=TREATMENTS.get(source_id, ""),
                country="Abroad" if source_id == 7 else "Mexico",
                heading_path=list(headings.values()),
                tables=tables,
            )
        )
        tables = []

    def walk(node):
        nonlocal heading
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            value = clean(str(node))
            if value:
                lines.append(value)
            return
        if not isinstance(node, Tag):
            return
        if node.name in ("h1", "h2", "h3", "h4"):
            flush()
            heading = clean(node.get_text(" ", strip=True))
            level = int(node.name[1])
            for key in list(headings):
                if key >= level:
                    del headings[key]
            headings[level] = heading
        elif node.name == "table":
            rows = table_rows(node)
            if rows:
                lines.extend(" | ".join(row) for row in rows)
                tables.append(EvidenceTable(headers=rows[0], rows=rows[1:]))
        elif node.name in ("p", "li") and not node.find(["h1", "h2", "h3", "h4", "table", "div", "section"]):
            lines.append(clean(node.get_text(" ", strip=True)))
        elif node.name not in ("img", "input", "select", "svg"):
            for child in node.children:
                walk(child)

    walk(root)
    flush()
    # Video grids can repeat the same card in the initial HTML. Evidence IDs are content addressed.
    sections = list({s.id: s for s in sections if len(s.text) > 12}.values())
    if not sections or sum(len(s.text) for s in sections) < 200:
        raise ExtractionError(f"Source {source_id}: unexpectedly empty principal content")
    prices: list[PriceFact] = []
    diagnostics: list[str] = []

    def evidence_for(text: str, hint: str = ""):
        candidates = [s for s in sections if hint.lower() in s.heading.lower()] if hint else sections
        return next((s for s in candidates if text in s.text), None) or next(
            (s for s in sections if text in s.text), None
        )

    def add_price(row, ev, **kwargs):
        if not ev:
            diagnostics.append("Price ignored: supporting evidence was not extracted")
            return
        money = parse_money(row)
        if not money:
            diagnostics.append("Price row ignored: amount is missing or ambiguous")
            return
        key = str(len(prices) + 1)
        prices.append(
            PriceFact(
                id=f"p{source_id}:{key}",
                source_id=source_id,
                evidence_id=ev.id,
                amount=money[0],
                high=money[1],
                **kwargs,
            )
        )

    if source_id in TREATMENTS:
        table = next((t for t in root.find_all("table") if "Total Cost (USD)" in t.get_text()), None)
        if table is None:
            raise ExtractionError(f"Source {source_id}: programme price table missing")
        exclusion_section = next((s for s in sections if "Excluded Services" in s.heading), None)
        exclusions = exclusion_section.text.splitlines() if exclusion_section else []
        for row in table_rows(table)[1:]:
            if len(row) != 3 or not row[0] or not parse_money(row[2]):
                raise ExtractionError(f"Source {source_id}: malformed programme row")
            ev = evidence_for(" | ".join(row))
            inclusion = next(
                (
                    s
                    for s in sections
                    if "Included in" in s.heading and row[0].split()[0].lower() in s.heading.lower()
                ),
                None,
            )
            add_price(
                row[2],
                ev,
                treatment=TREATMENTS[source_id],
                programme=row[0],
                duration=row[1],
                qualifier="starting_from" if row[0].lower().startswith("out") else "listed",
                inclusions=inclusion.text.splitlines() if inclusion else [],
                exclusions=exclusions,
                context_evidence_ids=[section.id for section in (inclusion, exclusion_section) if section],
            )
        if len(prices) != 2:
            raise ExtractionError(f"Source {source_id}: expected two auditable programmes")
        for section in sections:
            if "Optional Specialized" not in section.heading and "Excluded Services" not in section.heading:
                continue
            for line in section.text.splitlines():
                if not parse_money(line):
                    continue
                name = line.split(":", 1)[0]
                add_price(
                    line,
                    section,
                    treatment=name,
                    programme="Additional charge",
                    kind="addon",
                    qualifier="additional_cost",
                    description=line,
                    approximate="approx" in line.lower(),
                    unit=next(
                        (unit for unit in ("per visit", "per pint", "per area") if unit in line.lower()), ""
                    ),
                )
        # Append variable charges after numbered monetary records to preserve their stable identities.
        for section in sections:
            if "Excluded Services" in section.heading:
                for line in section.text.splitlines():
                    if "cost varies" in line.lower():
                        prices.append(
                            PriceFact(
                                id=f"p{source_id}:{len(prices) + 1}",
                                source_id=source_id,
                                evidence_id=section.id,
                                treatment=line.split(":", 1)[0],
                                amount=None,
                                programme="Additional charge",
                                kind="addon",
                                qualifier="varies",
                                description=line,
                            )
                        )
    elif source_id == 6:
        table = root.select_one("#prices table")
        if table is None or "Price in USD" not in table.get_text():
            raise ExtractionError("Source 6: expected USD clinic price table (currency cannot be inferred)")
        for row in table_rows(table)[1:]:
            if len(row) < 3 or not row[0]:
                diagnostics.append("Unnamed or malformed procedure row excluded")
                continue
            add_price(row[2], evidence_for(" | ".join(row)), treatment=row[0], kind="listing")
    elif source_id == 7:
        text = root.get_text(" ", strip=True)
        if "USD" not in text:
            raise ExtractionError("Source 7: explicit page currency missing")
        for table in root.find_all("table"):
            rows = table_rows(table)
            if not rows:
                continue
            for row in rows[1:]:
                ev = evidence_for(" | ".join(row))
                if rows[0][0] in ("Procedure", "Component") and len(row) >= 3:
                    add_price(
                        row[1] + " - " + row[2],
                        ev,
                        treatment=row[0],
                        country="Abroad",
                        clinic="",
                        kind="component" if rows[0][0] == "Component" else "category_range",
                        qualifier="starting_estimate",
                        description=row[3] if len(row) > 3 else "",
                    )
                elif "Clinic" in rows[0][0] and len(row) >= 3:
                    country = next(
                        (c for c in ("Thailand", "Croatia", "Turkey", "Mexico") if c in row[0]), "Abroad"
                    )
                    add_price(
                        row[1],
                        ev,
                        treatment=row[2],
                        clinic=row[0],
                        country=country,
                        kind="listing",
                        qualifier="listed",
                    )
                    if ev and prices and prices[-1].evidence_id == ev.id:
                        diagnostics.append(
                            f"Price {prices[-1].id}: unit and programme are unspecified; not a directly comparable total"
                        )
    elif source_id == 1 and "$" in root.get_text():
        diagnostics.append(
            "Profile dollar amounts kept as text; no explicit currency inferred for price records"
        )

    text = "\n".join(s.heading + "\n" + s.text for s in sections)
    return ExtractedPage(
        source_id=source_id,
        url=SOURCE_URLS[source_id],
        title=title,
        content_hash=digest(text),
        sections=sections,
        prices=prices,
        diagnostics=diagnostics,
        page_type=PAGE_TYPES[source_id],
        extractor_version=EXTRACTOR_VERSION,
    )


async def fetch_pages(config):
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=40, follow_redirects=False
    ) as client:
        robots = await client.get("https://www.placidway.com/robots.txt")
        robots.raise_for_status()
        rules = urllib.robotparser.RobotFileParser()
        rules.parse(robots.text.splitlines())
        delay = max(config.crawl_min_delay, rules.crawl_delay(USER_AGENT) or rules.crawl_delay("*") or 0)
        rate = rules.request_rate(USER_AGENT) or rules.request_rate("*")
        if rate:
            delay = max(delay, rate.seconds / rate.requests)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        folder = config.data_dir / "snapshots" / stamp
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "robots.txt").write_text(robots.text)
        pages = []
        for source_id, url in SOURCE_URLS.items():
            if not rules.can_fetch(USER_AGENT, url):
                raise ValueError(f"Source {source_id} disallowed by robots.txt")
            await asyncio.sleep(delay)
            response = await client.get(url)
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError(f"Source {source_id}: redirect requires allowlist review")
            (folder / f"page-{source_id}.html").write_text(response.text)
            page = extract_page(source_id, response.text)
            (folder / f"page-{source_id}.json").write_text(page.model_dump_json(indent=2))
            pages.append(page)
        return pages, folder


async def publish_pages(db: Database, embedder, pages: list[ExtractedPage], folder=None):
    if {p.source_id for p in pages} != set(SOURCE_URLS):
        raise ValueError("Publishing requires exactly the seven approved sources")
    active = await db.active_version()
    old = (
        await db.all(
            "SELECT source_id,content_hash,extractor_version,index_version FROM source_versions WHERE version=%s",
            (active,),
        )
        if active
        else []
    )
    old_hashes = {row["source_id"]: row["content_hash"] for row in old}
    old_meta = {row["source_id"]: row for row in old}
    index_version = INDEX_VERSION + ":" + db.config.embedding_model
    changed = [
        p
        for p in pages
        if old_hashes.get(p.source_id) != p.content_hash
        or old_meta[p.source_id]["extractor_version"] != p.extractor_version
        or old_meta[p.source_id]["index_version"] != index_version
    ]
    if not changed:
        return active, 0
    version = str(uuid4())
    async with db.pool.connection() as connection:
        async with connection.transaction():
            for page in pages:
                await connection.execute(
                    "INSERT INTO source_versions(version,source_id,url,title,content_hash,diagnostics,page_type,extractor_version,index_version,fetched_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        version,
                        page.source_id,
                        page.url,
                        page.title,
                        page.content_hash,
                        Jsonb(page.diagnostics),
                        page.page_type,
                        page.extractor_version,
                        index_version,
                        page.fetched_at,
                    ),
                )
                for section in page.sections:
                    await connection.execute(
                        "INSERT INTO evidence(version,id,source_id,payload) VALUES(%s,%s,%s,%s)",
                        (version, section.id, section.source_id, Jsonb(section.model_dump())),
                    )
                for price in page.prices:
                    await connection.execute(
                        "INSERT INTO price_facts(version,id,evidence_id,payload) VALUES(%s,%s,%s,%s)",
                        (version, price.id, price.evidence_id, Jsonb(price.model_dump(mode="json"))),
                    )
                if page not in changed and active:
                    await connection.execute(
                        "INSERT INTO chunks(version,id,evidence_id,text,embedding) SELECT %s,c.id,c.evidence_id,c.text,c.embedding FROM chunks c JOIN evidence e ON e.version=c.version AND e.id=c.evidence_id WHERE c.version=%s AND e.source_id=%s",
                        (version, active, page.source_id),
                    )
                else:
                    chunks = await asyncio.to_thread(embedder.chunks, page.sections)
                    vectors = await asyncio.to_thread(embedder.encode, [c[2] for c in chunks])
                    async with connection.cursor() as cursor:
                        await cursor.executemany(
                            "INSERT INTO chunks(version,id,evidence_id,text,embedding) VALUES(%s,%s,%s,%s,%s)",
                            [
                                (version, c[0], c[1], c[2], vector)
                                for c, vector in zip(chunks, vectors, strict=True)
                            ],
                        )
            await connection.execute(
                "INSERT INTO settings(key,value) VALUES('active_version',%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (version,),
            )
    if folder:
        (folder / "publication.json").write_text(
            json.dumps({"version": version, "changed_sources": [p.source_id for p in changed]})
        )
    return version, len(changed)


async def refresh(db: Database, embedder):
    async with db.pool.connection() as lock_connection:
        row = await (
            await lock_connection.execute("SELECT pg_try_advisory_lock(74190202) AS locked")
        ).fetchone()
        if not row["locked"]:
            return {"status": "already_running"}
        run_id = str(uuid4())
        await db.execute("INSERT INTO ingestion_runs(id,status) VALUES(%s,'running')", (run_id,))
        started = time.monotonic()
        try:
            pages, folder = await fetch_pages(db.config)
            version, count = await publish_pages(db, embedder, pages, folder)
            diagnostics = {
                "changed_sources": count,
                "duration_seconds": round(time.monotonic() - started, 2),
                "sources": {p.source_id: p.diagnostics for p in pages},
            }
            await db.execute(
                "UPDATE ingestion_runs SET status='completed',finished_at=now(),version=%s,diagnostics=%s WHERE id=%s",
                (version, Jsonb(diagnostics), run_id),
            )
            return {"status": "completed", "version": version, **diagnostics}
        except Exception as exc:
            # Only exception class is safe to persist; provider/header dumps are excluded.
            await db.execute(
                "UPDATE ingestion_runs SET status='failed',finished_at=now(),error=%s WHERE id=%s",
                (str(exc) if isinstance(exc, ExtractionError) else type(exc).__name__, run_id),
            )
            raise
        finally:
            await lock_connection.execute("SELECT pg_advisory_unlock(74190202)")
