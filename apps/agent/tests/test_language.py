"""Direct multilingual generation and legacy client hint isolation."""

from uuid import uuid4

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_workflow import FakeDatabase, FakeGenerator, PriceRetriever, run, search

from placidway.api import RunBody
from placidway.models import ReviewVerdict
from placidway.sources import SOURCE_URLS
from placidway.workflow import Workflow


async def test_direct_indonesian_answer_has_english_search_without_translation_stage():
    answer = AIMessage(content=f"Mulai dari USD 18,995 untuk program rawat jalan. [E1]({SOURCE_URLS[2]})")
    verdict = ReviewVerdict(supported=True, response_language="id", outcome="answered", used_price_ids=["p1"])
    generator = FakeGenerator([search(), answer], [verdict])
    retriever = PriceRetriever()
    workflow = Workflow(FakeDatabase(), retriever, generator, InMemorySaver())
    result, _ = await run(workflow, question="Berapa biaya rawat jalan adenokarsinoma?")
    assert result["response_language"] == "id"
    assert "Mulai dari" in result["content"]
    assert retriever.queries == ["adenocarcinoma outpatient price"]
    assert generator.review_payloads[0]["candidate"].startswith("Mulai dari")


def test_legacy_client_language_hint_is_ignored():
    body = RunBody(message="Bonjour", client_message_id=str(uuid4()), response_language="ar")
    assert not hasattr(body, "response_language")
