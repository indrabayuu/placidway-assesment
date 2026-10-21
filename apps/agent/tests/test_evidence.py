from decimal import Decimal

import pytest

from placidway.ingestion import extract_page, parse_money
from placidway.llm import parse_json, public_content
from placidway.models import AnswerBlock, AnswerDraft, Evidence, PriceFact, ResolvedRequest, Verification
from placidway.sources import SOURCE_URLS, allowed_url, treatment_source
from placidway.workflow import render, validate_draft


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


def test_numeric_cross_reference_requires_explicitly_cited_support():
    included = evidence().model_copy(update={"id": "included", "text": "One prescribed scan is included."})
    excluded = evidence().model_copy(
        update={"id": "excluded", "text": "Additional imaging beyond the included diagnostic is excluded."}
    )
    draft = AnswerDraft(
        outcome="answered",
        blocks=[
            AnswerBlock(kind="text", text="One prescribed scan is included.", evidence_ids=["included"]),
            AnswerBlock(
                kind="text",
                text="Imaging beyond the one prescribed scan is excluded.",
                evidence_ids=["excluded"],
            ),
        ],
    )
    request = ResolvedRequest(query="What is included?")
    assert not validate_draft(draft, [included, excluded], [], request)
    draft.blocks[1].text = "Fifty-five additional scans are included: 55."
    assert "unsupported_numeric_claim" in validate_draft(draft, [included, excluded], [], request)


def test_category_price_keeps_estimate_and_scope_limit():
    price = fact().model_copy(
        update={"qualifier": "starting_estimate", "kind": "category_range", "high": Decimal("30000")}
    )
    draft = AnswerDraft(outcome="answered", blocks=[AnswerBlock(kind="price", price_id=price.id)])
    text, _ = render(draft, [evidence()], [price], "en")
    assert "Estimated starting category range" in text
    assert "not a quote for a specific cancer programme" in text


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


def test_correct_number_wrong_treatment_fails():
    draft = AnswerDraft(outcome="answered", blocks=[AnswerBlock(kind="price", price_id="p1")])
    issues = validate_draft(
        draft, [evidence(3)], [fact(3)], ResolvedRequest(query="Adenocarcinoma Cancer price")
    )
    assert "wrong_treatment_price_source" in issues


def test_programme_mismatch_and_unstructured_price_rejected():
    draft = AnswerDraft(
        outcome="answered",
        blocks=[
            AnswerBlock(kind="price", price_id="p1"),
            AnswerBlock(kind="text", text="It costs USD 50", evidence_ids=["e1"]),
        ],
    )
    issues = validate_draft(
        draft, [evidence()], [fact()], ResolvedRequest(query="price", programme="inpatient")
    )
    assert {"wrong_programme", "monetary_text_must_use_price_record"} <= set(issues)


def test_server_renders_currency_qualifier_and_programme_in_spanish():
    draft = AnswerDraft(outcome="answered", blocks=[AnswerBlock(kind="price", price_id="p1")])
    text, citations = render(draft, [evidence()], [fact()], "es")
    assert all(term in text for term in ("Desde", "USD 18,995", "ambulatorio", "3 semanas"))
    assert citations[0]["url"] == SOURCE_URLS[2]


def test_generic_listing_does_not_establish_a_specific_programme():
    listing = fact().model_copy(update={"programme": "", "kind": "listing"})
    draft = AnswerDraft(outcome="answered", blocks=[AnswerBlock(kind="price", price_id="p1")])
    assert "wrong_programme" in validate_draft(
        draft, [evidence()], [listing], ResolvedRequest(query="price", programme="outpatient")
    )


def test_wrong_clinic_untrusted_links_and_unsupported_numbers_rejected():
    draft = AnswerDraft(
        outcome="answered",
        blocks=[
            AnswerBlock(kind="price", price_id="p1"),
            AnswerBlock(
                kind="text", text="Recover in 99 days. [Help](https://evil.example)", evidence_ids=["e1"]
            ),
        ],
    )
    issues = validate_draft(draft, [evidence()], [fact()], ResolvedRequest(query="price", clinic="Fizikon"))
    assert {"wrong_clinic", "unsupported_answer_url", "unsupported_numeric_claim"} <= set(issues)


def test_variable_prices_and_fractional_range_endpoints_preserve_context():
    variable = fact().model_copy(update={"amount": None, "qualifier": "varies", "kind": "addon"})
    draft = AnswerDraft(outcome="answered", blocks=[AnswerBlock(kind="price", price_id="p1")])
    text, _ = render(draft, [evidence()], [variable], "en")
    assert "Price varies" in text and "USD 0" not in text
    precise = fact().model_copy(update={"amount": Decimal("100.25"), "high": Decimal("200.75")})
    text, _ = render(draft, [evidence()], [precise], "en")
    assert "100.25–200.75" in text


def test_url_allowlist_is_exact():
    assert allowed_url(SOURCE_URLS[1])
    assert not allowed_url(SOURCE_URLS[1] + "-injected")
    assert not allowed_url("https://evil.example/" + SOURCE_URLS[1])
    assert treatment_source("personalized treatment") is None
    assert treatment_source("Anal cancer programme") == 4
