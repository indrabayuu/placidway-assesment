"""Provider-tag parsing works at every token boundary without answer leakage."""

import pytest

from placidway.streaming import ThinkingParser


@pytest.mark.parametrize("tag", ["reasoning", "think", "analysis"])
def test_every_split_keeps_thinking_separate(tag):
    text = f"<{tag}>A provisional thought.</{tag}>A final answer."
    for split in range(len(text) + 1):
        parser = ThinkingParser()
        parts = parser.feed(text[:split]) + parser.feed(text[split:]) + parser.feed("", final=True)
        assert "".join(t for kind, t in parts if kind == "thinking.delta") == "A provisional thought."
        assert "".join(t for kind, t in parts if kind == "answer.delta") == "A final answer."


def test_incomplete_reasoning_cannot_be_accepted():
    parser = ThinkingParser()
    parser.feed("<reasoning>unfinished")
    with pytest.raises(ValueError):
        parser.feed("", final=True)
