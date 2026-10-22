"""Separate the configured provider's tagged reasoning from answer deltas."""

from langchain_core.callbacks import AsyncCallbackHandler


class ThinkingParser:
    """Incremental parser; tags may be split at any token boundary."""

    tags = tuple((f"<{name}>", True) for name in ("reasoning", "think", "analysis")) + tuple(
        (f"</{name}>", False) for name in ("reasoning", "think", "analysis")
    )

    def __init__(self):
        self.buffer = ""
        self.thinking = False

    def feed(self, text, final=False):
        self.buffer += text
        result = []
        while self.buffer:
            lower = self.buffer.lower()
            matches = [(lower.find(tag), tag, mode) for tag, mode in self.tags if tag in lower]
            if matches:
                index, tag, mode = min(matches)
                if index:
                    result.append(
                        ("thinking.delta" if self.thinking else "answer.delta", self.buffer[:index])
                    )
                self.buffer = self.buffer[index + len(tag) :]
                self.thinking = mode
                continue
            keep = 0
            if not final:
                for size in range(1, min(len(self.buffer), 13) + 1):
                    if any(tag.startswith(lower[-size:]) for tag, _ in self.tags):
                        keep = size
            count = len(self.buffer) - keep
            if count:
                result.append(("thinking.delta" if self.thinking else "answer.delta", self.buffer[:count]))
                self.buffer = self.buffer[count:]
            break
        if final and (self.thinking or self.buffer):
            raise ValueError("Incomplete provider reasoning block")
        return result


class MainStream(AsyncCallbackHandler):
    """Attached only to the main model, never the advisor or reviewer."""

    raise_error = True

    def __init__(self, turn):
        self.turn = turn
        self.parser = ThinkingParser()

    async def on_chat_model_start(self, serialized, messages, **kwargs):
        self.parser = ThinkingParser()
        self.turn.attempt += 1
        self.turn.candidate_id = f"{self.turn.run_id}:{self.turn.attempt}"
        await self.turn.emit(
            "answer.start",
            {
                "message_id": self.turn.run_id,
                "candidate_id": self.turn.candidate_id,
                "response_language": "auto",
                "provisional": True,
            },
        )
        await self.turn.stage("generate", "Preparing a response")

    async def on_visible_thinking(self, text):
        await self.turn.emit(
            "thinking.delta", {"text": text, "candidate_id": self.turn.candidate_id, "provisional": True}
        )

    async def on_llm_new_token(self, token, **kwargs):
        for event, text in self.parser.feed(token):
            if text:
                await self.turn.emit(
                    event, {"text": text, "candidate_id": self.turn.candidate_id, "provisional": True}
                )

    async def on_llm_end(self, response, **kwargs):
        for event, text in self.parser.feed("", final=True):
            if text:
                await self.turn.emit(
                    event, {"text": text, "candidate_id": self.turn.candidate_id, "provisional": True}
                )
