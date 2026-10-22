"""Load readable system prompts without double-escaping their line breaks."""

from importlib.resources import files
from textwrap import dedent
from typing import Literal

from langchain_core.messages import SystemMessage

PromptName = Literal["main", "advisor", "review"]


def normalize_system_prompt(text: str) -> str:
    """Normalize source indentation and platform line endings, preserving Markdown.

    JSON encoders escape actual newlines on the wire; decoders restore them. Do not
    pre-escape newlines or decode arbitrary escape sequences from prompt content.
    """
    normalized = dedent(text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not normalized:
        raise ValueError("System prompt cannot be empty")
    return normalized


def load_system_prompt(name: PromptName) -> str:
    if name not in ("main", "advisor", "review"):
        raise ValueError("Unknown system prompt")
    source = files("placidway").joinpath("system_prompts", f"{name}.md")
    return normalize_system_prompt(source.read_text(encoding="utf-8"))


def system_message(name: PromptName) -> SystemMessage:
    """Convert a named prompt into a chat message, retaining actual line breaks."""
    return SystemMessage(content=load_system_prompt(name))


MAIN = load_system_prompt("main")
ADVISOR = load_system_prompt("advisor")
REVIEW = load_system_prompt("review")
