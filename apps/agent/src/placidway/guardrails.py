"""Evidence identity and monetary checks independent of model review."""

import re
from decimal import Decimal, InvalidOperation
from unicodedata import normalize

from placidway.sources import QUOTE_URL

CITATION = re.compile(r"\[(E\d+)\]\((https?://[^\s)]+)\)")
LINK = re.compile(r"\]\(([^)]+)\)")
NUMBER = r"\d(?:[\d,.\u066b\u066c\u00a0\u202f ]*\d)?"
MONEY = re.compile(rf"(?:(USD|EUR|MXN|\$|€|£)\s*({NUMBER})|({NUMBER})\s*(USD|EUR|MXN|dollars|dólares))", re.I)


def decimal_number(value):
    value = normalize("NFKC", value).replace("\u066c", "").replace("\u066b", ".").replace(" ", "")
    if "," in value and "." in value:
        decimal_mark = "," if value.rfind(",") > value.rfind(".") else "."
        value = value.replace("." if decimal_mark == "," else ",", "").replace(decimal_mark, ".")
    elif "," in value:
        value = value.replace(",", "") if len(value.rsplit(",", 1)[1]) == 3 else value.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        value = value.replace(".", "")
    return Decimal(value)


def numeric_values(content):
    # URLs and evidence labels are metadata, not monetary range endpoints.
    content = re.sub(r"https?://\S+|\[E\d+\]", "", content)
    values = set()
    for value in re.findall(NUMBER, content):
        try:
            values.add(decimal_number(value))
        except InvalidOperation:
            continue
    return values


def monetary_values(text):
    values = []
    for match in MONEY.finditer(text):
        currency, number, after_number, after_currency = match.groups()
        currency = (currency or after_currency).upper()
        currency = {"$": "USD", "DOLLARS": "USD", "DÓLARES": "USD", "€": "EUR", "£": "GBP"}.get(
            currency, currency
        )
        try:
            values.append((currency, decimal_number(number or after_number)))
        except InvalidOperation:
            continue
    return values


def inspect_answer(content, documents):
    """Return deterministic issues and citations, without trusting reviewer IDs."""
    labels = {doc["label"]: doc for doc in documents}
    citations = []
    issues = []
    for label, url in CITATION.findall(content):
        doc = labels.get(label)
        if doc is None or doc["evidence"]["url"] != url:
            issues.append("citation_not_in_available_evidence")
        elif not any(c["id"] == doc["evidence"]["id"] for c in citations):
            evidence = doc["evidence"]
            citations.append(
                {"id": evidence["id"], "url": url, "title": evidence["title"], "section": evidence["heading"]}
            )
    permitted = {doc["evidence"]["url"] for doc in documents} | {QUOTE_URL}
    if any(url not in permitted for url in LINK.findall(content)):
        issues.append("unsupported_answer_url")
    for url in re.findall(r"https?://[^\s)<>\]]+", content):
        if url.rstrip(".,") not in permitted:
            issues.append("unsupported_answer_url")
    cited_ids = {c["id"] for c in citations}
    facts = [fact for doc in documents if doc["evidence"]["id"] in cited_ids for fact in doc["prices"]]
    approved = {
        (f["currency"], Decimal(str(amount)))
        for f in facts
        for amount in (f["amount"], f.get("high"))
        if amount is not None
    }
    if any(value not in approved for value in monetary_values(content)):
        issues.append("unsupported_monetary_value_or_citation")
    if (
        re.search(r"\[[^\]]+\]\([^)]*\)", content)
        and not citations
        and any(url != QUOTE_URL for url in LINK.findall(content))
    ):
        issues.append("citations_require_supplied_E_labels")
    return sorted(set(issues)), citations


def normalize_citations(content, documents):
    """Resolve known evidence markers to links without altering factual prose."""
    labels = {doc["label"]: doc["evidence"]["url"] for doc in documents}
    content = re.sub(r"【(E\d+)】", r"[\1]", content)
    content = re.sub(r"\[[\u200b\u200c\u200d\ufeff]*(E\d+)[\u200b\u200c\u200d\ufeff]*\]", r"[\1]", content)

    def link(match):
        label = match.group(1)
        return f"[{label}]({labels[label]})" if label in labels else match.group()

    return re.sub(r"\[(E\d+)\](?!\()", link, content)
