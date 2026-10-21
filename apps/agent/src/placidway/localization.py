"""Localize approved answers without allowing model-generated prices or links."""

import re
from uuid import uuid4

from placidway.llm import StructuredOutputError
from placidway.models import LocalizedAnswer, Verification
from placidway.prompts import LOCALIZE, VERIFY_LOCALIZATION

# Keep citations, URLs, currency codes/acronyms and every written digit exact.
PROTECTED = re.compile(
    r"\[\d+\]\(https?://[^\s)]+\)|https?://[^\s)]+|(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])|\d[\d,]*(?:\.\d+)?"
)


class LocalizationError(RuntimeError):
    """A translation could not be validated; do not publish a wrong-language answer."""


def protected_values(content):
    return PROTECTED.findall(content)


async def localize(generator, content, language, question, repair_budget):
    if language.split("-")[0] == "en":
        return content
    prefix = "__PW_KEEP_" + uuid4().hex + "_"
    values = []

    def protect(match):
        token = f"{prefix}{len(values)}__"
        values.append((token, match.group()))
        return token

    masked = PROTECTED.sub(protect, content)
    try:
        translated = await generator.call(
            LocalizedAnswer,
            LOCALIZE,
            {"target_language": language, "question": question, "approved_answer": masked},
            repair_budget,
        )
        text = translated.content
        tokens = re.findall(re.escape(prefix) + r"\d+__", text)
        if tokens != [token for token, _ in values]:
            raise LocalizationError("Translation changed protected fact ordering or identity")
        remainder = text
        for token, value in values:
            remainder = remainder.replace(token, "")
            text = text.replace(token, value)
        if "__PW_KEEP_" in remainder or PROTECTED.search(remainder):
            raise LocalizationError("Translation introduced a number, currency code or URL")
        if protected_values(text) != protected_values(content):
            raise LocalizationError("Translation changed protected facts")
        verification = await generator.call(
            Verification,
            VERIFY_LOCALIZATION,
            {"approved_answer": content, "translated_answer": text, "target_language": language},
            repair_budget,
        )
        if not verification.supported:
            raise LocalizationError("Translation failed language or meaning verification")
        return text
    except StructuredOutputError as exc:
        raise LocalizationError("Translation could not be validated") from exc
