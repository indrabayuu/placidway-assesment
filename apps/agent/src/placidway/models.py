"""Evidence and answer contracts shared by ingestion, runtime and evaluation."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Language = Literal["en", "es", "ar"]
Outcome = Literal[
    "answered",
    "partial",
    "unknown",
    "clarification",
    "medical_referral",
    "out_of_scope",
    "unsupported_action",
]


class EvidenceTable(BaseModel):
    headers: list[str]
    rows: list[list[str]]


class Evidence(BaseModel):
    id: str
    source_id: int
    url: str
    title: str
    heading: str
    text: str
    kind: str = "section"
    treatment: str = ""
    country: str = "Mexico"
    programme: str = ""
    heading_path: list[str] = Field(default_factory=list)
    tables: list[EvidenceTable] = Field(default_factory=list)


class PriceFact(BaseModel):
    id: str
    source_id: int
    evidence_id: str
    treatment: str
    clinic: str = "ITC Immunity Therapy Center"
    country: str = "Mexico"
    programme: str = ""
    amount: Decimal | None = Field(ge=0)
    high: Decimal | None = Field(default=None, ge=0)
    currency: str = "USD"
    qualifier: str = "listed"
    duration: str = ""
    unit: str = ""
    kind: Literal["programme", "addon", "component", "listing", "category_range"] = "programme"
    description: str = ""
    inclusions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    approximate: bool = False
    context_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_range(self):
        if self.amount is None and (self.qualifier != "varies" or self.high is not None):
            raise ValueError("A missing amount must be explicitly marked as variable")
        if self.high is not None and self.high < self.amount:
            raise ValueError("Price range is inverted")
        return self


class ExtractedPage(BaseModel):
    source_id: int
    url: str
    title: str
    content_hash: str
    sections: list[Evidence]
    prices: list[PriceFact]
    diagnostics: list[str] = Field(default_factory=list)
    page_type: str = "unknown"
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    extractor_version: str = "4"


class ResolvedRequest(BaseModel):
    query: str
    intent: Literal[
        "cost",
        "comparison",
        "inclusions",
        "general",
        "medical_referral",
        "out_of_scope",
        "unsupported_action",
        "clarification",
    ] = "general"
    treatment: str = ""
    country: str = ""
    programme: str = ""
    clinic: str = ""
    response_language: Language = "en"


class AnswerBlock(BaseModel):
    kind: Literal["text", "price"]
    text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    price_id: str = ""


class AnswerDraft(BaseModel):
    outcome: Outcome
    blocks: list[AnswerBlock] = Field(default_factory=list)
    offer_quote: bool = False
    medical_disclaimer: bool = False


class Verification(BaseModel):
    supported: bool
    issues: list[str] = Field(default_factory=list)


class Coverage(BaseModel):
    sufficient: bool
    missing: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    content: str
    outcome: Outcome
    response_language: Language
    citations: list[dict] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    version: str | None = None
    diagnostics: dict = Field(default_factory=dict)
