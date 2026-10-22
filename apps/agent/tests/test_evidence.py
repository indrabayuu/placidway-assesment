from decimal import Decimal

import pytest

from placidway.ingestion import extract_page, parse_money
from placidway.llm import parse_json, public_content
from placidway.models import Evidence, PriceFact, Verification
from placidway.sources import SOURCE_URLS, allowed_url, treatment_source


def test_money_ranges_and_decimal_precision():
    assert parse_money("Approx. $3,950 - $4,250 USD") == (Decimal("3950"), Decimal("4250"))
    assert parse_money("$462.50 per pint") == (Decimal("462.50"), None)
    assert parse_money("Cost varies") is None


def test_price_table_does_not_infer_unnamed_procedure():
    html = """<h1>ITC price list</h1><main class="article-dt-desc"><div class="pack-inner-content"><section id="prices"><h2>Pricings</h2><table><tr><th>Procedure</th><th>Details</th><th>Price in USD</th></tr><tr><td>Breast Cancer</td><td>Clinic listed price</td><td>$18995</td></tr><tr><td></td><td></td><td>$18995</td></tr></table></section><p>This page lists clinic procedures and their prices. Additional programme details must be requested from the clinic and cannot be inferred from the price alone.</p></div></main>"""
    page = extract_page(6, html)
    assert len(page.prices) == 1
    assert page.prices[0].treatment == "Breast Cancer"
    assert page.prices[0].programme == ""
    assert any("Unnamed" in item for item in page.diagnostics)


def test_duplicate_video_cards_have_one_evidence_identity():
    card = "<h3>Patient video title</h3><p>ITC Immunity Therapy Center, Tijuana, Mexico. A listing title provides no transcript or proof of treatment outcomes. This publicly visible listing describes the video title and location only, and makes no promise about an individual patient outcome or eligibility.</p>"
    page = extract_page(
        5,
        '<h1>Videos</h1><main class="article-dt-desc"><div class="pack-inner-content">'
        + card * 3
        + "</div></main>",
    )
    assert len(page.sections) == 1
    assert page.sections[0].kind == "video_listing"


def test_missing_principal_content_fails_closed():
    with pytest.raises(ValueError, match="principal"):
        extract_page(2, "<h1>Unavailable</h1><p>Cookie wall</p>")


def test_comments_and_related_recommendations_are_not_evidence():
    html = """<h1>ITC price list</h1><main class="article-dt-desc"><div class="pack-inner-content">
    <section id="prices"><h2>Pricings</h2><table><tr><th>Procedure</th><th>Details</th><th>Price in USD</th></tr>
    <tr><td>Breast Cancer</td><td>Clinic listed price</td><td>$18995</td></tr></table></section>
    <p>This page lists clinic procedures and their prices. Additional programme details must be requested from the clinic and cannot be inferred from the price alone. Consult the clinic for individual assessment and an itemized quote.</p>
    <!-- obsolete-promotion: a historical price should never be extracted -->
    <p><h3>Related Experiences:</h3><a href="/experience/1">Unrelated dental implant promotion</a></p>
    </div></main>"""
    page = extract_page(6, html)
    text = "\n".join(section.text for section in page.sections)
    assert "obsolete-promotion" not in text
    assert "dental implant" not in text


def test_reasoning_is_removed_before_json_or_display():
    content = '<reasoning>Private internal text</reasoning>{"supported":true,"issues":[]}'
    assert "Private" not in public_content(content)
    assert parse_json(content, Verification).supported
    with pytest.raises(ValueError, match="Unclosed"):
        public_content("<think>unfinished")


def evidence(source=2):
    return Evidence(
        id="e1",
        source_id=source,
        url=SOURCE_URLS[source],
        title="Package",
        heading="Cost",
        text="Outpatient USD 18,995, three weeks",
    )


def fact(source=2):
    return PriceFact(
        id="p1",
        source_id=source,
        evidence_id="e1",
        treatment="Adenocarcinoma Cancer",
        programme="Out-Patient Program",
        amount=Decimal("18995"),
        duration="3 Weeks",
        qualifier="starting_from",
    )


def test_url_allowlist_is_exact():
    assert allowed_url(SOURCE_URLS[1])
    assert not allowed_url(SOURCE_URLS[1] + "-injected")
    assert not allowed_url("https://evil.example/" + SOURCE_URLS[1])
    assert treatment_source("personalized treatment") is None
    assert treatment_source("Anal cancer programme") == 4
