"""One create_agent conversation loop with tools and a bounded evidence review."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command, Overwrite

from placidway.guardrails import inspect_answer, monetary_values, normalize_citations, numeric_values
from placidway.llm import CALLS, public_content
from placidway.models import Answer, ReviewVerdict
from placidway.prompts import ADVISOR, MAIN, REVIEW
from placidway.sources import QUOTE_URL
from placidway.streaming import MainStream


def merge_documents(old, new):
    documents = {item["key"]: item for item in old}
    for item in new:
        prior = documents.get(item["key"], {})
        documents[item["key"]] = {
            **item,
            "answer_ids": list(dict.fromkeys(prior.get("answer_ids", []) + item.get("answer_ids", []))),
        }
    return list(documents.values())


class ConversationState(AgentState):
    documents: Annotated[list[dict], merge_documents]
    history_documents: Annotated[list[dict], merge_documents]
    answer: dict
    agent_version: int


@dataclass
class Turn:
    run_id: str = ""
    message_id: str = ""
    question: str = ""
    response_language: str = "en"
    version: str = ""
    stage_sink: object = None
    event_sink: object = None
    attempt: int = 0
    candidate_id: str = ""
    searches: int = 0
    consultations: int = 0
    revisions: int = 0
    fallback: bool = False
    feedback: str = ""
    model_calls: int = 0
    documents: list = field(default_factory=list)
    activity: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    reviews: list = field(default_factory=list)
    history: list = field(default_factory=list)
    blocked_runs: set = field(default_factory=set)
    labels: dict = field(default_factory=dict)
    next_label: int = 1
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    repair_budget: list = field(default_factory=lambda: [1])

    async def emit(self, name, data):
        if self.event_sink:
            await self.event_sink(name, {"run_id": self.run_id, **data})

    async def stage(self, name, message):
        item = {"stage": name, "message": message}
        self.activity.append(item)
        if self.stage_sink:
            await self.stage_sink(item)

    def available(self, state):
        return merge_documents(self.history, state.get("documents", []))


class EvidenceMiddleware(AgentMiddleware):
    state_schema = ConversationState

    def __init__(self, workflow, turn):
        self.workflow, self.turn = workflow, turn

    async def awrap_model_call(self, request, handler):
        turn = self.turn
        turn.model_calls += 1
        if turn.model_calls > 10:
            raise RuntimeError("Agent model-call budget exhausted")
        messages = request.messages
        current = next(
            (i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and m.id == turn.message_id),
            len(messages),
        )
        # Old tool chatter/rejected answers are not carried into later turns.
        prefix = [
            m
            for m in messages[:current]
            if isinstance(m, HumanMessage)
            or (
                isinstance(m, AIMessage)
                and m.additional_kwargs.get("accepted")
                and m.additional_kwargs.get("run_id") not in turn.blocked_runs
            )
        ]
        tail = messages[current:]
        # Retain complete message/tool pairs for the active turn. Bound older chat context.
        messages = prefix[-12:] + tail
        context = {
            "verified_contact_url": QUOTE_URL,
            "corpus_version": turn.version,
            "historical_documents": turn.history,
            "remaining_searches": 3 - turn.searches,
            "remaining_consultations": 1 - turn.consultations,
        }
        system = (
            request.system_message.content
            + "\n\n## Application context (data)\n"
            + json.dumps(context, ensure_ascii=False)
        )
        if turn.feedback:
            system += "\n\n## Application review feedback\n" + turn.feedback
        if turn.fallback:
            system += "\n\nThe revision budget is exhausted. Give only a brief limitation explanation in the user's language, with no medical or price facts. Tools are unavailable."
        started = time.monotonic()
        response = await handler(
            request.override(
                messages=messages,
                system_message=SystemMessage(system),
                tools=[] if turn.fallback else request.tools,
            )
        )
        for message in response.result:
            self.workflow.generator.record("main", message, started)
        return response

    async def awrap_tool_call(self, request, handler):
        turn = self.turn
        name = request.tool_call["name"]
        async with turn.lock:
            attr, limit = ("searches", 3) if name == "knowledge_base_search" else ("consultations", 1)
            count = getattr(turn, attr)
            if turn.fallback or count >= limit:
                turn.tool_calls.append({"name": name, "args": request.tool_call["args"], "blocked": True})
                return ToolMessage(
                    content="Tool budget exhausted. Use available evidence and honestly identify gaps.",
                    tool_call_id=request.tool_call["id"],
                )
            setattr(turn, attr, count + 1)
            turn.tool_calls.append({"name": name, "args": request.tool_call["args"], "blocked": False})
        # Retrieval/provider failures propagate as operational errors, not empty evidence.
        return await handler(request)

    @hook_config(can_jump_to=["model", "end"])
    async def aafter_model(self, state, runtime):
        turn = self.turn
        original = state["messages"][-1]
        content = public_content(original.content)
        candidate = original.model_copy(
            update={"content": content, "additional_kwargs": {"provisional": True}}
        )
        if candidate.tool_calls:
            await turn.emit(
                "answer.rejected", {"candidate_id": turn.candidate_id, "reason": "Continuing with tools"}
            )
            return {"messages": [candidate]}
        await turn.stage("review", "Checking the answer against the sources")
        documents = turn.available(state)
        content = normalize_citations(content, documents)
        candidate.content = content
        issues, citations = inspect_answer(content, documents)
        verdict = None
        if self.workflow.runtime_guardrails:
            await turn.stage("review", "Checking the answer against the sources")
            verdict = await self.workflow.generator.call(
                ReviewVerdict,
                REVIEW,
                {
                    "question": turn.question,
                    "candidate": content,
                    "documents": documents,
                    "verified_contact_url": QUOTE_URL,
                    "limitation_only": turn.fallback,
                },
                turn.repair_budget,
            )
            supplied = {p["id"] for doc in documents for p in doc["prices"]}
            if not set(verdict.used_price_ids) <= supplied:
                issues.append("reviewer_referenced_unavailable_price")
            issues = sorted(
                set(issues + ([] if verdict.supported else (verdict.issues or ["unsupported_answer"])))
            )
            turn.reviews.append(
                {
                    "candidate_id": turn.candidate_id,
                    "accepted": not issues,
                    "issues": issues,
                    "verdict": verdict.model_dump(),
                    "candidate": content,
                }
            )
        else:
            # Ablation mode: keep prompt instructions and tool budgets, but do not
            # reject, revise, or review the main agent's answer. This is only
            # enabled by explicit evaluation construction, never by API settings.
            issues = []
        if issues:
            await turn.emit(
                "answer.rejected",
                {
                    "candidate_id": turn.candidate_id,
                    "reason": "Revising an answer that did not pass source checks",
                },
            )
            candidate.additional_kwargs["rejected"] = True
            if turn.fallback:
                raise RuntimeError("Even the limitation response failed review")
            if turn.revisions:
                turn.fallback = True
            else:
                turn.revisions += 1
            await turn.stage("revise", "Revising the response using the available evidence")
            instruction = (
                "The revision budget is exhausted. Give only a brief explanation that a sufficiently supported answer could not be produced, in the user's language. You may offer the verified contact link. Include no price, medical claim, or package detail."
                if turn.fallback
                else "Correct the specific issues in the user's language using evidence and any remaining tools."
            )
            guidance = (
                " Each monetary value must be supported by its cited price record. Remove unsolicited prices instead of repeating an uncited base-price comparison."
                if "unsupported_monetary_value_or_citation" in issues
                else ""
            )
            turn.feedback = json.dumps(issues) + ". " + instruction + guidance
            return {"messages": [candidate], "jump_to": "model"}
        if not content:
            raise RuntimeError("The provider returned an empty answer")
        created = datetime.now(timezone.utc).isoformat()
        # Ablation runs have no reviewer to supply this internal metadata.
        # Resolve `auto` to a valid placeholder; judges assess answer text.
        response_language = (
            verdict.response_language
            if verdict
            else turn.response_language if turn.response_language != "auto" else "en"
        )
        metadata = {
            "accepted": True,
            "created_at": created,
            "citations": citations,
            "activity": turn.activity,
            "outcome": verdict.outcome if verdict else "answered",
            "response_language": response_language,
            "run_id": turn.run_id,
        }
        candidate.additional_kwargs = metadata
        prices = list({p["id"]: p for doc in documents for p in doc["prices"]}.values())
        # Reviewer metadata must describe monetary values actually represented,
        # not every service whose description happens to be mentioned.
        from decimal import Decimal

        cited_ids = {citation["id"] for citation in citations}
        written_money = set(monetary_values(content))
        written_numbers = numeric_values(content)
        represented = [
            price["id"]
            for price in prices
            if price["evidence_id"] in cited_ids
            and (
                (
                    price["id"] in verdict.used_price_ids
                    and (price["amount"] is None or (price["currency"], Decimal(str(price["amount"]))) in written_money)
                )
                if verdict is not None
                else price["amount"] is not None and Decimal(str(price["amount"])) in written_numbers
            )
        ]
        # Diagnostics contain only evidence the model actually had, never golden context.
        answer = Answer(
            content=content,
            outcome=verdict.outcome if verdict else "answered",
            response_language=response_language,
            citations=citations,
            evidence_ids=[c["id"] for c in citations],
            version=turn.version,
            diagnostics={
                "documents": documents,
                "validation_issues": [],
                "used_price_ids": represented,
                "evidence": [doc["evidence"] for doc in documents],
                "prices": prices,
                "tool_calls": turn.tool_calls,
                "reviews": turn.reviews,
                "search_count": turn.searches,
                "consultation_count": turn.consultations,
                "revision_count": turn.revisions,
            },
        ).model_dump()
        cited = {c["id"] for c in citations}
        used_documents = [
            {**doc, "answer_ids": [turn.run_id]} for doc in documents if doc["evidence"]["id"] in cited
        ]
        if verdict:
            await turn.emit("review.complete", {"candidate_id": turn.candidate_id, "accepted": True})
        return {
            "messages": [candidate],
            "answer": answer,
            "history_documents": used_documents,
            "jump_to": "end",
        }


class Workflow:
    def __init__(self, db, retriever, generator, checkpointer, pinned_version=None, runtime_guardrails=True):
        self.db, self.retriever, self.generator, self.checkpointer = db, retriever, generator, checkpointer
        self.pinned_version = pinned_version
        self.runtime_guardrails = runtime_guardrails

    def graph(self, turn=None):
        turn = turn or Turn()

        @tool
        async def knowledge_base_search(query: str, runtime: ToolRuntime) -> Command:
            """Search approved PlacidWay evidence with an English query. Preserve the exact requested treatment and detail; reformulate if needed. At most three searches per turn."""
            await turn.stage("search", "Searching the approved knowledge base")
            sections, prices, hits = await self.retriever.search_documents(query, turn.version)
            async with turn.lock:
                documents = []
                for section in sections:
                    key = turn.version + ":" + section.id
                    if key not in turn.labels:
                        turn.labels[key] = f"E{turn.next_label}"
                        turn.next_label += 1
                    documents.append(
                        {
                            "key": key,
                            "label": turn.labels[key],
                            "citation": f"[{turn.labels[key]}]({section.url})",
                            "version": turn.version,
                            "evidence": section.model_dump(mode="json"),
                            "prices": [
                                p.model_dump(mode="json") for p in prices if p.evidence_id == section.id
                            ],
                            "answer_ids": [],
                        }
                    )
                turn.documents = merge_documents(turn.documents, documents)
                # Each retrieval is bounded to 8k tokens and there are at most
                # three. Retain all actually supplied evidence for final review.
            await turn.stage("search_complete", f"Found {len(documents)} source sections")
            result = json.dumps(
                {"query": query, "documents": documents, "remaining_searches": 3 - turn.searches},
                ensure_ascii=False,
            )
            return Command(
                update={
                    "documents": documents,
                    "messages": [ToolMessage(content=result, tool_call_id=runtime.tool_call_id)],
                }
            )

        @tool
        async def consult_evidence_advisor(question: str, runtime: ToolRuntime) -> str:
            """Optionally ask a neutral advisor about ambiguous evidence, missing facts, or incompatible comparisons. Once per turn; advice is not factual evidence."""
            await turn.stage("consult", "Consulting the evidence advisor")
            try:
                advice = await self.generator.advice(
                    ADVISOR,
                    {
                        "user_question": turn.question,
                        "uncertainty": question,
                        "documents": turn.available(runtime.state),
                    },
                )
            except Exception:
                await turn.stage("consult_unavailable", "The evidence advisor is unavailable")
                return "The advisor is unavailable. Use available evidence; this failure does not establish that information is missing."
            await turn.stage("consult_complete", "Evidence consultation completed")
            return advice

        model = self.generator.model("main").model_copy(update={"callbacks": [MainStream(turn)]})
        return create_agent(
            model,
            tools=[knowledge_base_search, consult_evidence_advisor],
            system_prompt=MAIN,
            state_schema=ConversationState,
            middleware=[EvidenceMiddleware(self, turn)],
            checkpointer=self.checkpointer,
            name="placidway_agent",
        )

    @staticmethod
    def config(conversation_id):
        # A new thread namespace leaves all legacy checkpoints intact and readable.
        return {"configurable": {"thread_id": f"agent-v2:{conversation_id}"}, "recursion_limit": 40}

    async def unpublished_runs(self, messages):
        """A reviewed checkpoint is not published until its API run commits."""
        if not hasattr(self.db, "all"):
            return set()
        ids = []
        for message in messages:
            key = message.additional_kwargs.get("run_id")
            if key:
                try:
                    ids.append(str(UUID(key)))
                except ValueError:
                    continue
        if not ids:
            return set()
        rows = await self.db.all("SELECT id,status FROM runs WHERE id=ANY(%s::uuid[])", (ids,))
        return {str(row["id"]) for row in rows if row["status"] != "completed"}

    async def legacy_messages(self, conversation_id):
        checkpoint = await self.checkpointer.aget_tuple({"configurable": {"thread_id": str(conversation_id)}})
        if not checkpoint:
            return []
        messages = checkpoint.checkpoint.get("channel_values", {}).get("messages", [])
        return [
            m.model_copy(update={"additional_kwargs": {**m.additional_kwargs, "accepted": True}})
            for m in messages
            if isinstance(m, (AIMessage, HumanMessage))
        ]

    async def run(self, conversation_id, question, language, message_id, run_id, stage=None, stream=None):
        version = self.pinned_version or await self.db.active_version()
        if not version:
            raise RuntimeError("Knowledge base not initialized")
        started = time.monotonic()
        calls = []
        token = CALLS.set(calls)
        turn = Turn(
            run_id=run_id,
            message_id=message_id,
            question=question,
            response_language=language,
            version=version,
            stage_sink=stage,
            event_sink=stream,
        )
        graph = self.graph(turn)
        config = self.config(conversation_id)
        try:
            snapshot = await graph.aget_state(config)
            values = snapshot.values or {}
            turn.labels = {doc["key"]: doc["label"] for doc in values.get("history_documents", [])}
            turn.next_label = max([int(label[1:]) for label in turn.labels.values()] or [0]) + 1
            turn.blocked_runs = await self.unpublished_runs(values.get("messages", []))
            # Keep an audit record of old versions, but only supply current evidence to the model.
            turn.history = [
                doc
                for doc in values.get("history_documents", [])
                if doc["version"] == version
                and any(key not in turn.blocked_runs for key in doc.get("answer_ids", []))
            ]
            # Bound selected historical context without deleting persisted provenance.
            turn.history = turn.history[-8:]
            prior = [] if values.get("messages") else await self.legacy_messages(conversation_id)
            human = HumanMessage(
                content=question,
                id=message_id,
                additional_kwargs={"created_at": datetime.now(timezone.utc).isoformat()},
            )
            result = await graph.ainvoke(
                {"messages": prior + [human], "documents": Overwrite([]), "answer": {}, "agent_version": 2},
                config,
            )
            answer = result["answer"]
            answer["diagnostics"].update({"calls": calls, "seconds": round(time.monotonic() - started, 3)})
            last = result["messages"][-1]
            message = {
                "id": str(last.id),
                "role": "assistant",
                "content": last.content,
                **last.additional_kwargs,
            }
            return answer, message
        except Exception as exc:
            exc.diagnostics = {
                "reviews": turn.reviews,
                "tool_calls": turn.tool_calls,
                "search_count": turn.searches,
            }
            raise
        finally:
            CALLS.reset(token)

    async def history(self, conversation_id):
        checkpoint = await self.checkpointer.aget_tuple(self.config(conversation_id))
        values = checkpoint.checkpoint.get("channel_values", {}) if checkpoint else {}
        messages = values.get("messages", [])
        if not messages:
            messages = await self.legacy_messages(conversation_id)
        blocked = await self.unpublished_runs(messages)
        return [
            {
                "id": str(m.id),
                "role": "user" if isinstance(m, HumanMessage) else "assistant",
                "content": public_content(m.content),
                "citations": [],
                "activity": [],
                **m.additional_kwargs,
            }
            for m in messages
            if isinstance(m, HumanMessage)
            or (
                isinstance(m, AIMessage)
                and m.additional_kwargs.get("accepted")
                and m.additional_kwargs.get("run_id") not in blocked
            )
        ]
