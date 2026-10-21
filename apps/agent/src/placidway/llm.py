import json
import re
import time
from contextvars import ContextVar

from langchain_openai import ChatOpenAI

CALLS: ContextVar[list | None] = ContextVar("provider_calls", default=None)


class StructuredOutputError(ValueError):
    """The bounded schema repair failed; no unvalidated content may be displayed."""


def public_content(content) -> str:
    if isinstance(content, list):
        content = "".join(
            item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
        )
    text = str(content)
    text = re.sub(r"<(reasoning|think|analysis)\b[^>]*>.*?</\1>", "", text, flags=re.S | re.I)
    if re.search(r"<(reasoning|think|analysis)\b", text, flags=re.I):
        raise ValueError("Unclosed reasoning block: no public output accepted")
    return text.strip()


def parse_json(content, schema):
    text = public_content(content)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    return schema.model_validate_json(text)


class Generator:
    def __init__(self, config):
        self.config = config
        self._client = None

    @property
    def client(self):
        if not self.config.generation_ready:
            raise RuntimeError("Generation provider configuration is incomplete")
        if self._client is None:
            self._client = ChatOpenAI(
                model=self.config.openai_model,
                base_url=self.config.openai_base_url,
                api_key=self.config.openai_api_key,
                use_responses_api=False,
                reasoning_effort=self.config.openai_reasoning_effort or None,
                max_tokens=4096,
                timeout=self.config.provider_timeout,
                max_retries=2,
            )
        return self._client

    async def call(self, schema, instructions: str, payload: dict, repair_budget: list[int]):
        system = (
            instructions
            + "\nReturn a single JSON object matching this schema, with no reasoning or prose outside JSON:\n"
            + json.dumps(schema.model_json_schema())
        )
        messages = [("system", system), ("user", json.dumps(payload, ensure_ascii=False, default=str))]
        started = time.monotonic()
        response = await self.client.ainvoke(messages)
        calls = CALLS.get()
        if calls is not None:
            calls.append(
                {
                    "step": schema.__name__,
                    "seconds": round(time.monotonic() - started, 3),
                    "usage": response.usage_metadata,
                }
            )
        try:
            return parse_json(response.content, schema)
        except (ValueError, TypeError):
            if not repair_budget[0]:
                raise StructuredOutputError("Structured output failed validation") from None
            repair_budget[0] -= 1
            # Never send or persist hidden reasoning in a repair prompt.
            try:
                candidate = public_content(response.content)
            except ValueError:
                candidate = "{}"
            repaired = await self.client.ainvoke(
                messages
                + [
                    ("assistant", candidate),
                    (
                        "user",
                        "The JSON failed validation. Return only corrected JSON that exactly follows the schema. Do not add facts.",
                    ),
                ]
            )
            if calls is not None:
                calls.append({"step": "json_repair", "usage": repaired.usage_metadata})
            try:
                return parse_json(repaired.content, schema)
            except (ValueError, TypeError):
                raise StructuredOutputError("Structured output failed validation after repair") from None
