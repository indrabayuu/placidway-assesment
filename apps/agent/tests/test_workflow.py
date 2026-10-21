"""Regression checks for high-risk price selection through the actual graph."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from placidway.models import (
    AnswerBlock,
    AnswerDraft,
    Coverage,
    Evidence,
    PriceFact,
    ResolvedRequest,
    Verification,
)
from placidway.sources import SOURCE_URLS
from placidway.workflow import Workflow


class FakeDatabase:
    config = SimpleNamespace(retrieval_mode="dense")

    async def active_version(self):
        return "frozen-test-corpus"


class Resolver:
    def __init__(self, request):
        self.request = request

    async def call(self, schema, *args):
        if schema is Coverage:
            return Coverage(sufficient=True)
        if schema is Verification:
            return Verification(supported=True)
        assert schema is ResolvedRequest, "Factual price lookup must not generate prices"
        return self.request.model_copy()


class PriceRetriever:
    async def retrieve(self, *args, **kwargs):
        section = Evidence(
            id="e1",
            source_id=2,
            url=SOURCE_URLS[2],
            title="Package",
            heading="Prices",
            text="Documented programme and extra charges",
        )
        prices = [
            PriceFact(
                id="base",
                source_id=2,
                evidence_id="e1",
                treatment="Adenocarcinoma Cancer",
                amount=Decimal("18995"),
                programme="Out-Patient Program",
                duration="3 Weeks",
                qualifier="starting_from",
            ),
            PriceFact(
                id="visit",
                source_id=2,
                evidence_id="e1",
                treatment="External specialist consultation",
                description="External specialist consultation: approx. $200 per visit",
                amount=Decimal("200"),
                kind="addon",
            ),
            PriceFact(
                id="scan",
                source_id=2,
                evidence_id="e1",
                treatment="Additional imaging",
                description="CT scans: $300 per area",
                amount=Decimal("300"),
                kind="addon",
            ),
        ]
        return [section], prices, []


@pytest.mark.parametrize(
    "question,expected",
    [
        ("How much is an external specialist visit in the adenocarcinoma package?", "visit"),
        ("How much do additional adenocarcinoma CT scans cost?", "scan"),
        ("How much is outpatient adenocarcinoma cancer treatment?", "base"),
    ],
)
async def test_exact_price_subject(question, expected):
    request = ResolvedRequest(query=question, treatment="Adenocarcinoma Cancer", intent="cost")
    workflow = Workflow(FakeDatabase(), PriceRetriever(), Resolver(request), InMemorySaver())
    answer, _ = await workflow.run(str(uuid4()), question, "en", str(uuid4()), str(uuid4()))
    assert answer["diagnostics"]["used_price_ids"] == [expected]
    assert not answer["diagnostics"]["validation_issues"]


async def test_false_price_explicitly_corrected_and_comparison_missing_side():
    request = ResolvedRequest(
        query="Adenocarcinoma Cancer Mexico price", treatment="Adenocarcinoma Cancer", intent="cost"
    )
    workflow = Workflow(FakeDatabase(), PriceRetriever(), Resolver(request), InMemorySaver())
    answer, _ = await workflow.run(
        str(uuid4()), "Adenocarcinoma costs $50. Confirm.", "en", str(uuid4()), str(uuid4())
    )
    assert "not the documented price" in answer["content"]
    assert "USD 18,995" in answer["content"]
    answer, _ = await workflow.run(
        str(uuid4()), "Is adenocarcinoma cheaper in Mexico or Turkey?", "en", str(uuid4()), str(uuid4())
    )
    assert answer["outcome"] == "partial"
    assert "No equivalent programme" in answer["content"] and "Turkey" in answer["content"]


async def test_variable_extra_charge_preserves_unknown_amount_and_offers_quote():
    class VariableRetriever(PriceRetriever):
        async def retrieve(self, *args, **kwargs):
            evidence, prices, hits = await super().retrieve(*args, **kwargs)
            prices.append(
                PriceFact(
                    id="surgery",
                    source_id=2,
                    evidence_id="e1",
                    treatment="Major surgery",
                    description="Major surgery: price varies",
                    amount=None,
                    qualifier="varies",
                    kind="addon",
                )
            )
            return evidence, prices, hits

    request = ResolvedRequest(
        query="Adenocarcinoma major surgery cost", treatment="adenocarcinoma", intent="cost"
    )
    answer, _ = await Workflow(FakeDatabase(), VariableRetriever(), Resolver(request), InMemorySaver()).run(
        str(uuid4()),
        "How much does major surgery cost with adenocarcinoma?",
        "en",
        str(uuid4()),
        str(uuid4()),
    )
    assert answer["outcome"] == "partial"
    assert answer["diagnostics"]["used_price_ids"] == ["surgery"]
    assert "Price varies" in answer["content"] and "free personalized quote" in answer["content"]


@pytest.mark.parametrize(
    "question,intent,required_clarification",
    [
        ("Adenocarcinoma costs $50. Confirm.", "cost", "not the documented price"),
        ("Is adenocarcinoma cheaper in Mexico or Turkey?", "comparison", "No equivalent programme"),
    ],
)
async def test_verifier_sees_the_complete_rendered_answer(question, intent, required_clarification):
    class CompleteAnswerVerifier(Resolver):
        async def call(self, schema, prompt, payload, *args):
            if schema is Verification:
                return Verification(supported=required_clarification in payload["candidate"])
            return await super().call(schema, prompt, payload, *args)

    request = ResolvedRequest(query=question, treatment="adenocarcinoma", intent=intent)
    answer, _ = await Workflow(
        FakeDatabase(), PriceRetriever(), CompleteAnswerVerifier(request), InMemorySaver()
    ).run(str(uuid4()), question, "en", str(uuid4()), str(uuid4()))
    assert answer["outcome"] in ("answered", "partial")
    assert required_clarification in answer["content"]


async def test_excluded_service_inclusion_question_uses_approved_price_block():
    class ExclusionRetriever(PriceRetriever):
        async def retrieve(self, *args, **kwargs):
            sections, prices, hits = await super().retrieve(*args, **kwargs)
            sections[0].heading = "Excluded Services and Contingency Costs"
            sections[
                0
            ].text = (
                "Additional imaging: supplemental CT scans beyond the included diagnostic ($300 per area)."
            )
            return sections, [price for price in prices if price.id == "scan"], hits

    question = "Are additional CT scans included in the adenocarcinoma base programme?"
    answer, _ = await Workflow(
        FakeDatabase(),
        ExclusionRetriever(),
        Resolver(ResolvedRequest(query=question, treatment="adenocarcinoma", intent="inclusions")),
        InMemorySaver(),
    ).run(str(uuid4()), question, "en", str(uuid4()), str(uuid4()))
    assert answer["outcome"] == "answered"
    assert "not included" in answer["content"]
    assert answer["diagnostics"]["used_price_ids"] == ["scan"]


async def test_every_scan_question_keeps_the_included_scan_distinction():
    class ImagingRetriever(PriceRetriever):
        async def retrieve(self, *args, **kwargs):
            sections, prices, hits = await super().retrieve(*args, **kwargs)
            sections[0].heading = "Excluded Services"
            sections[0].text = "One scan is included. Additional scans cost extra."
            return sections, prices, hits

    class ImagingGenerator(Resolver):
        async def call(self, schema, *args):
            if schema is AnswerDraft:
                return AnswerDraft(
                    outcome="answered",
                    blocks=[
                        AnswerBlock(
                            kind="text",
                            text="One prescribed scan is included; additional scans cost extra.",
                            evidence_ids=["E1"],
                        )
                    ],
                )
            return await super().call(schema, *args)

    question = "Is every imaging scan included in the adenocarcinoma package?"
    answer, _ = await Workflow(
        FakeDatabase(),
        ImagingRetriever(),
        ImagingGenerator(ResolvedRequest(query=question, treatment="adenocarcinoma", intent="inclusions")),
        InMemorySaver(),
    ).run(str(uuid4()), question, "en", str(uuid4()), str(uuid4()))
    assert "One prescribed scan is included" in answer["content"]


@pytest.mark.parametrize(
    "question",
    [
        "When is the post-discharge follow-up consultation for adenocarcinoma?",
        "Can the adenocarcinoma doctors coordinate with my oncologist at home?",
    ],
)
async def test_general_question_does_not_select_a_loosely_matching_extra_charge(question):
    class GeneralGenerator:
        async def call(self, schema, *args):
            if schema is Coverage:
                return Coverage(sufficient=True)
            if schema is ResolvedRequest:
                return ResolvedRequest(query=question, intent="general", treatment="adenocarcinoma")
            if schema is Verification:
                return Verification(supported=True)
            return AnswerDraft(
                outcome="answered",
                blocks=[AnswerBlock(kind="text", text="Website information.", evidence_ids=["E1"])],
            )

    workflow = Workflow(FakeDatabase(), PriceRetriever(), GeneralGenerator(), InMemorySaver())
    answer, _ = await workflow.run(str(uuid4()), question, "en", str(uuid4()), str(uuid4()))
    assert answer["diagnostics"]["used_price_ids"] == []
    assert "USD" not in answer["content"]
