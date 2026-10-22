"""Exercise the real create_agent loop with deterministic model/tool fixtures."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from placidway.models import Evidence, PriceFact, ReviewVerdict
from placidway.sources import SOURCE_URLS
from placidway.workflow import Workflow


class FakeDatabase:
    config = SimpleNamespace(retrieval_mode="hybrid")

    async def active_version(self):
        return "test-corpus"


class PriceRetriever:
    queries = None

    def __init__(self):
        self.queries = []

    async def search_documents(self, query, version):
        self.queries.append(query)
        section = Evidence(
            id="e1",
            source_id=2,
            url=SOURCE_URLS[2],
            title="Adenocarcinoma package",
            heading="Prices",
            text="Outpatient programme starting from USD 18,995 for 3 weeks. Recovery time is not specified.",
        )
        price = PriceFact(
            id="p1",
            source_id=2,
            evidence_id="e1",
            treatment="Adenocarcinoma Cancer",
            amount=18995,
            programme="Out-Patient Program",
            qualifier="starting_from",
        )
        return [section], [price], []


class ScriptedModel(BaseChatModel):
    responses: list
    seen: list = []

    @property
    def _llm_type(self):
        return "scripted-test-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, **kwargs):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        response = self.responses.pop(0).model_copy(deep=True)
        if response.id is None:
            response.id = str(uuid4())
        if run_manager and response.content:
            await run_manager.on_llm_new_token(response.content)
        return ChatResult(generations=[ChatGeneration(message=response)])


class FakeGenerator:
    def __init__(self, responses, verdicts=None):
        self.main = ScriptedModel(responses=responses)
        self.verdicts = verdicts or []
        self.review_payloads = []

    def model(self, role):
        assert role == "main"
        return self.main

    def record(self, *args):
        pass

    async def advice(self, instructions, payload):
        return "The supplied evidence does not specify recovery time."

    async def call(self, schema, instructions, payload, budget):
        self.review_payloads.append(payload)
        return (
            self.verdicts.pop(0)
            if self.verdicts
            else ReviewVerdict(
                supported=True,
                response_language="en",
                outcome="answered",
                used_price_ids=["p1"] if "18,995" in payload["candidate"] else [],
            )
        )


def search(query="adenocarcinoma outpatient price", count=1):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "knowledge_base_search", "args": {"query": query}, "id": str(uuid4())}
            for _ in range(count)
        ],
    )


def cost():
    return AIMessage(content=f"Starting from USD 18,995 for the outpatient programme. [E1]({SOURCE_URLS[2]})")


async def run(workflow, conversation=None, question="Outpatient price?", stream=None):
    return await workflow.run(
        conversation or str(uuid4()), question, "auto", str(uuid4()), str(uuid4()), stream=stream
    )


async def test_natural_price_answer_streams_then_reviews_and_promotes_only_cited_evidence():
    generator = FakeGenerator([search(), cost()])
    workflow = Workflow(FakeDatabase(), PriceRetriever(), generator, InMemorySaver())
    events = []

    async def stream(name, data):
        events.append((name, data))

    conversation = str(uuid4())
    answer, message = await run(workflow, conversation, stream=stream)
    assert answer["diagnostics"]["used_price_ids"] == ["p1"]
    assert not answer["diagnostics"]["validation_issues"]
    assert len(await workflow.history(conversation)) == 2
    snapshot = await workflow.graph().aget_state(workflow.config(conversation))
    assert [d["evidence"]["id"] for d in snapshot.values["history_documents"]] == ["e1"]
    names = [name for name, _ in events]
    assert names.index("answer.delta") < names.index("review.complete")
    assert message["accepted"]


async def test_parallel_search_limit_and_revision_share_one_budget():
    generator = FakeGenerator([search(count=4), cost()])
    retriever = PriceRetriever()
    answer, _ = await run(Workflow(FakeDatabase(), retriever, generator, InMemorySaver()))
    assert len(retriever.queries) == answer["diagnostics"]["search_count"] == 3
    assert sum(call["blocked"] for call in answer["diagnostics"]["tool_calls"]) == 1


async def test_rejected_candidate_is_replaced_and_never_restored_as_accepted_history():
    verdicts = [
        ReviewVerdict(
            supported=False, issues=["invented recovery duration"], response_language="en", outcome="answered"
        ),
        ReviewVerdict(supported=True, response_language="en", outcome="unknown"),
    ]
    generator = FakeGenerator(
        [
            search(),
            AIMessage(content="Recovery takes 9 days."),
            AIMessage(content="The pages do not specify a recovery time."),
        ],
        verdicts,
    )
    workflow = Workflow(FakeDatabase(), PriceRetriever(), generator, InMemorySaver())
    conversation = str(uuid4())
    answer, _ = await run(workflow, conversation)
    assert answer["diagnostics"]["revision_count"] == 1
    assert "9 days" not in str(await workflow.history(conversation))
    assert not (await workflow.graph().aget_state(workflow.config(conversation))).values["history_documents"]


async def test_review_failure_is_operational_and_does_not_accept_candidate():
    class Unavailable(FakeGenerator):
        async def call(self, *args):
            raise ConnectionError("reviewer unavailable")

    workflow = Workflow(FakeDatabase(), PriceRetriever(), Unavailable([search(), cost()]), InMemorySaver())
    conversation = str(uuid4())
    with pytest.raises(ConnectionError):
        await run(workflow, conversation)
    assert all(m["role"] != "assistant" for m in await workflow.history(conversation))


async def test_historical_evidence_is_reused_but_not_when_corpus_version_changes():
    generator = FakeGenerator(
        [
            search(),
            cost(),
            cost(),
            search(),
            cost().model_copy(update={"content": cost().content.replace("[E1]", "[E2]")}),
        ]
    )
    db = FakeDatabase()
    workflow = Workflow(db, PriceRetriever(), generator, InMemorySaver())
    conversation = str(uuid4())
    await run(workflow, conversation)
    answer, _ = await run(workflow, conversation, "And the price again?")
    assert answer["diagnostics"]["search_count"] == 0
    assert answer["diagnostics"]["evidence"]
    db.active_version = lambda: asyncio_sleep_version()
    await run(workflow, conversation, "What is the current price?")
    assert workflow.retriever.queries == ["adenocarcinoma outpatient price"] * 2
    assert all(d["version"] == "test-corpus-new" for d in generator.review_payloads[-1]["documents"])


async def asyncio_sleep_version():
    return "test-corpus-new"


async def test_consultation_is_optional_and_limited_even_for_parallel_requests():
    consultation = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "consult_evidence_advisor",
                "args": {"question": "Is recovery duration documented?"},
                "id": str(uuid4()),
            }
            for _ in range(2)
        ],
    )
    generator = FakeGenerator([search(), consultation, cost()])
    answer, _ = await run(Workflow(FakeDatabase(), PriceRetriever(), generator, InMemorySaver()))
    assert answer["diagnostics"]["consultation_count"] == 1
    assert sum(c["blocked"] for c in answer["diagnostics"]["tool_calls"]) == 1


async def test_two_rejections_allow_only_one_revision_then_a_fact_free_limitation():
    rejected = ReviewVerdict(
        supported=False, issues=["unsupported claim"], response_language="en", outcome="answered"
    )
    accepted = ReviewVerdict(supported=True, response_language="en", outcome="unknown")
    generator = FakeGenerator(
        [
            search(),
            AIMessage(content="Recovery takes 9 days."),
            AIMessage(content="Recovery takes 8 days."),
            AIMessage(content="I could not produce a sufficiently supported answer."),
        ],
        [rejected, rejected, accepted],
    )
    workflow = Workflow(FakeDatabase(), PriceRetriever(), generator, InMemorySaver())
    conversation = str(uuid4())
    answer, _ = await run(workflow, conversation)
    assert answer["diagnostics"]["revision_count"] == 1
    assert len(answer["diagnostics"]["reviews"]) == 3
    assert len(await workflow.history(conversation)) == 2
    assert not (await workflow.graph().aget_state(workflow.config(conversation))).values["history_documents"]


async def test_legacy_messages_are_adapted_without_overwriting_legacy_checkpoint():
    from langchain_core.messages import HumanMessage

    conversation = str(uuid4())
    legacy = [HumanMessage("Old question", id="old-user"), AIMessage("Old verified answer", id="old-ai")]

    class LegacySaver(InMemorySaver):
        async def aget_tuple(self, config):
            if config["configurable"]["thread_id"] == conversation:
                return SimpleNamespace(checkpoint={"channel_values": {"messages": legacy}})
            return await super().aget_tuple(config)

    generator = FakeGenerator([AIMessage(content="Hello, how can I help?")])
    workflow = Workflow(FakeDatabase(), PriceRetriever(), generator, LegacySaver())
    await run(workflow, conversation, "Hello")
    history = await workflow.history(conversation)
    assert len(history) == 4
    assert history[0]["content"] == "Old question"
    assert not legacy[1].additional_kwargs.get("accepted")


async def test_uncommitted_run_cannot_appear_as_completed_history():
    class PublishingDatabase(FakeDatabase):
        status = "running"
        run_id = str(uuid4())

        async def all(self, query, params):
            return [{"id": self.run_id, "status": self.status}]

    db = PublishingDatabase()
    workflow = Workflow(db, PriceRetriever(), FakeGenerator([search(), cost()]), InMemorySaver())
    conversation = str(uuid4())
    await workflow.run(conversation, "Price?", "auto", str(uuid4()), db.run_id)
    assert len(await workflow.history(conversation)) == 1
    db.status = "completed"
    assert len(await workflow.history(conversation)) == 2
    # Reading completed history must not require an initialized generation client.
    workflow.generator = None
    assert len(await workflow.history(conversation)) == 2


async def test_optional_advisor_failure_does_not_erase_available_evidence():
    class UnavailableAdvisor(FakeGenerator):
        async def advice(self, *args):
            raise ConnectionError("advisor unavailable")

    consultation = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "consult_evidence_advisor",
                "args": {"question": "Is the price qualified?"},
                "id": str(uuid4()),
            }
        ],
    )
    workflow = Workflow(
        FakeDatabase(),
        PriceRetriever(),
        UnavailableAdvisor([search(), consultation, cost()]),
        InMemorySaver(),
    )
    answer, _ = await run(workflow)
    assert "18,995" in answer["content"]
    assert answer["diagnostics"]["consultation_count"] == 1


async def test_retrieving_an_older_document_keeps_its_original_citation_label():
    generator = FakeGenerator([search(), cost()])
    retriever = PriceRetriever()
    workflow = Workflow(FakeDatabase(), retriever, generator, InMemorySaver())
    conversation = str(uuid4())
    sections, prices, _ = await retriever.search_documents("seed", "test-corpus")
    history = [
        {
            "key": f"test-corpus:e{i}",
            "label": f"E{i}",
            "version": "test-corpus",
            "evidence": sections[0].model_copy(update={"id": f"e{i}"}).model_dump(),
            "prices": [prices[0].model_dump(mode="json")] if i == 1 else [],
            "answer_ids": ["prior-answer"],
        }
        for i in range(1, 13)
    ]
    await workflow.graph().aupdate_state(
        workflow.config(conversation),
        {"messages": [], "documents": [], "history_documents": history},
        as_node="__start__",
    )
    answer, _ = await run(workflow, conversation)
    assert answer["citations"][0]["id"] == "e1"
    stored = (await workflow.graph().aget_state(workflow.config(conversation))).values["history_documents"]
    assert next(doc["label"] for doc in stored if doc["key"] == "test-corpus:e1") == "E1"


async def test_later_search_evidence_is_not_discarded_by_earlier_large_results():
    class LargeRetriever(PriceRetriever):
        async def search_documents(self, query, version):
            sections, prices, hits = await super().search_documents(query, version)
            sections[0].text += " background" * 6000
            if query == "first query":
                sections[0].id = "unhelpful"
                return sections, [], hits
            return sections, prices, hits

    response = AIMessage(content=f"Starting from USD 18,995. [E2]({SOURCE_URLS[2]})")
    workflow = Workflow(
        FakeDatabase(),
        LargeRetriever(),
        FakeGenerator([search("first query"), search("better query"), response]),
        InMemorySaver(),
    )
    answer, _ = await run(workflow)
    assert len(answer["diagnostics"]["documents"]) == 2
    assert answer["diagnostics"]["used_price_ids"] == ["p1"]
