"""Automatic language propagation and protected localization contract checks."""

from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_workflow import FakeDatabase, PriceRetriever, Resolver

from placidway.api import RunBody
from placidway.localization import LocalizationError, localize, protected_values
from placidway.models import LocalizedAnswer, ResolvedRequest, Verification
from placidway.workflow import Workflow


class IndonesianGenerator(Resolver):
    async def call(self, schema, prompt, payload, *args):
        if schema is LocalizedAnswer:
            content = payload["approved_answer"]
            for source, target in {
                "Starting from": "Mulai dari",
                "Adenocarcinoma Cancer": "Kanker adenokarsinoma",
                "Outpatient programme": "Program rawat jalan",
                "Mexico": "Meksiko",
                "weeks": "minggu",
                "I can help with the treatments, clinics, packages, and prices described on the approved PlacidWay pages.": "Saya dapat membantu menjelaskan perawatan, klinik, paket, dan harga di halaman PlacidWay yang disetujui.",
            }.items():
                content = content.replace(source, target)
            return LocalizedAnswer(content=content)
        return await super().call(schema, prompt, payload, *args)


async def test_detected_language_overrides_legacy_hint_and_keeps_english_retrieval():
    question = "Berapa biaya rawat jalan kanker adenokarsinoma di Meksiko?"
    request = ResolvedRequest(
        query="Adenocarcinoma cancer outpatient treatment cost in Mexico",
        intent="cost",
        treatment="Adenocarcinoma Cancer",
        country="Mexico",
        country_mention="Meksiko",
        programme="outpatient",
        programme_mention="rawat jalan",
        response_language="id",
    )

    class InspectRetriever(PriceRetriever):
        async def retrieve(self, resolved, *args, **kwargs):
            assert resolved.query == request.query
            assert resolved.country == "Mexico" and resolved.programme == "outpatient"
            return await super().retrieve(resolved, *args, **kwargs)

    workflow = Workflow(FakeDatabase(), InspectRetriever(), IndonesianGenerator(request), InMemorySaver())
    conversation = str(uuid4())
    answer, message = await workflow.run(conversation, question, "ar", str(uuid4()), str(uuid4()))
    assert answer["response_language"] == message["response_language"] == "id"
    assert "Mulai dari" in answer["content"] and "Program rawat jalan" in answer["content"]
    assert "USD 18,995" in answer["content"] and "3 minggu" in answer["content"]
    assert protected_values(answer["content"]) == protected_values(answer["diagnostics"]["canonical_content"])
    assert (await workflow.history(conversation))[-1]["response_language"] == "id"


async def test_boundary_answer_is_localized_too():
    workflow = Workflow(
        FakeDatabase(),
        PriceRetriever(),
        IndonesianGenerator(
            ResolvedRequest(query="Weather in Cancun", intent="out_of_scope", response_language="id")
        ),
        InMemorySaver(),
    )
    answer, _ = await workflow.run(
        str(uuid4()), "Bagaimana cuaca di Cancun?", "auto", str(uuid4()), str(uuid4())
    )
    assert answer["outcome"] == "out_of_scope" and answer["response_language"] == "id"
    assert answer["content"].startswith("Saya dapat membantu")


@pytest.mark.parametrize("tamper", ["number", "url", "duplicate", "order", "meaning"])
async def test_localization_rejects_changed_facts_and_meaning(tamper):
    class TamperingGenerator:
        async def call(self, schema, prompt, payload, *args):
            if schema is Verification:
                return Verification(supported=tamper != "meaning")
            text = payload["approved_answer"]
            import re

            tokens = re.findall(r"__PW_KEEP_[a-f0-9]+_\d+__", text)
            if tamper == "number":
                text += " 50"
            if tamper == "url":
                text += " https://example.org"
            if tamper == "duplicate":
                text += tokens[0]
            if tamper == "order":
                text = (
                    text.replace(tokens[0], "TEMP").replace(tokens[1], tokens[0]).replace("TEMP", tokens[1])
                )
            return LocalizedAnswer(content=text)

    with pytest.raises(LocalizationError):
        await localize(
            TamperingGenerator(),
            "Starting from USD 18,995 [1](https://www.placidway.com/package/7903)",
            "id",
            "Biaya?",
            [1],
        )


def test_response_language_is_not_a_client_control_and_accepts_any_language_tag():
    body = RunBody.model_validate(
        {"message": "Bonjour", "client_message_id": str(uuid4()), "response_language": "ar"}
    )
    assert "response_language" not in body.model_dump()
    for language in ("id", "ja", "fr", "he", "hi", "zh-hant"):
        assert (
            ResolvedRequest(query="English retrieval query", response_language=language).response_language
            == language
        )
