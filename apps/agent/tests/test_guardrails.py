from test_evidence import evidence, fact

from placidway.guardrails import inspect_answer, normalize_citations


def documents():
    return [{"label": "E1", "evidence": evidence().model_dump(), "prices": [fact().model_dump(mode="json")]}]


def test_known_markers_resolve_without_correcting_untrusted_links():
    url = evidence().url
    for marker in ("[E1]", "【E1】", f"【E1】({url})"):
        assert normalize_citations(marker, documents()) == f"[E1]({url})"
    bad = "[E1](https://evil.example)"
    assert normalize_citations(bad, documents()) == bad
    assert inspect_answer(bad, documents())[0]


def test_amount_must_match_cited_price_not_just_any_available_number():
    url = evidence().url
    assert not inspect_answer(f"Starting from USD 18,995. [E1]({url})", documents())[0]
    assert inspect_answer(f"USD 50. [E1]({url})", documents())[0]
    assert inspect_answer("USD 18,995.", documents())[0]
    assert inspect_answer(f"USD 18,995. [E99]({url})", documents())[0]


def test_localized_digits_and_grouping_preserve_exact_prices():
    from decimal import Decimal

    from placidway.guardrails import monetary_values, numeric_values

    for text in ("USD 18,995", "USD ١٨٬٩٩٥", "USD 18.995", "USD 18\u202f995"):
        assert monetary_values(text) == [("USD", Decimal("18995"))]
    assert numeric_values("USD 100.25–200.75") == {Decimal("100.25"), Decimal("200.75")}
    assert monetary_values("EUR 1.234,50") == [("EUR", Decimal("1234.50"))]
