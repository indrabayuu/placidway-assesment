"""Model clients and bounded structured output for internal review only."""

import json
import re
import time
from contextvars import ContextVar

from langchain_openai import ChatOpenAI

CALLS: ContextVar[list | None] = ContextVar("provider_calls", default=None)


class StructuredOutputError(ValueError):
    """An internal model verdict could not be validated."""


def public_content(content) -> str:
    if isinstance(content, list):
        content = "".join(
            item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
        )
    text = str(content)
    text = re.sub(r"<(reasoning|think|analysis)\b[^>]*>.*?</\1>", "", text, flags=re.S | re.I)
    if re.search(r"<(reasoning|think|analysis)\b", text, flags=re.I):
        raise ValueError("Unclosed reasoning block: no public answer accepted")
    return text.strip()


def parse_json(content, schema):
    text = public_content(content)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    return schema.model_validate_json(text)


class Generator:
    def __init__(self, config):
        self.config = config
        self._clients = {}

    def model(self, role="main"):
        if not self.config.generation_ready:
            raise RuntimeError("Generation provider configuration is incomplete")
        if role not in self._clients:
            effort = (
                self.config.main_reasoning_effort if role == "main" else self.config.helper_reasoning_effort
            )
            client_type = ConversationalModel if role == "main" else ChatOpenAI
            self._clients[role] = client_type(
                model=self.config.openai_model,
                base_url=self.config.openai_base_url,
                api_key=self.config.openai_api_key,
                use_responses_api=False,
                reasoning_effort=effort,
                streaming=role == "main",
                max_tokens=8192,
                timeout=self.config.provider_timeout,
                max_retries=2,
            )
        return self._clients[role]

    async def advice(self, instructions, payload):
        started = time.monotonic()
        response = await self.model("advisor").ainvoke(
            [("system", instructions), ("user", json.dumps(payload, ensure_ascii=False, default=str))]
        )
        self.record("advisor", response, started)
        return public_content(response.content)

    @staticmethod
    def record(step, response, started):
        calls = CALLS.get()
        if calls is not None:
            calls.append(
                {
                    "step": step,
                    "seconds": round(time.monotonic() - started, 3),
                    "usage": response.usage_metadata,
                }
            )

    async def call(self, schema, instructions, payload, repair_budget):
        system = (
            instructions
            + "\nReturn one JSON object matching this schema, without prose outside JSON:\n"
            + json.dumps(schema.model_json_schema())
        )
        messages = [("system", system), ("user", json.dumps(payload, ensure_ascii=False, default=str))]
        started = time.monotonic()
        response = await self.model("review").ainvoke(messages)
        self.record(schema.__name__, response, started)
        try:
            return parse_json(response.content, schema)
        except (ValueError, TypeError):
            if not repair_budget[0]:
                raise StructuredOutputError("Internal verdict failed validation") from None
            repair_budget[0] -= 1
            # A protocol repair is not another opportunity to add evidence or facts.
            repaired = await self.model("review").ainvoke(
                messages
                + [
                    (
                        "user",
                        "Return a valid JSON verdict matching the schema. Do not include reasoning or new facts.",
                    )
                ]
            )
            self.record("verdict_json_repair", repaired, time.monotonic())
            try:
                return parse_json(repaired.content, schema)
            except (ValueError, TypeError):
                raise StructuredOutputError("Internal verdict repair failed") from None


class ConversationalModel(ChatOpenAI):
    """Stream provider thinking to callbacks, but persist only answer/tool content."""

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        from placidway.streaming import ThinkingParser

        parser = ThinkingParser()
        async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            if isinstance(chunk.message.content, str):
                parts = parser.feed(chunk.message.content)
                for kind, text in parts:
                    if kind == "thinking.delta" and isinstance(self.callbacks, list):
                        for callback in self.callbacks:
                            if hasattr(callback, "on_visible_thinking"):
                                await callback.on_visible_thinking(text)
                chunk.message.content = "".join(text for kind, text in parts if kind == "answer.delta")
            yield chunk
        # A malformed reasoning block is an operational error, never an accepted answer.
        parser.feed("", final=True)
