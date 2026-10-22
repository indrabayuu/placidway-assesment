"""Prompt loading preserves human-readable structure through chat serialization."""

import json

import pytest

from placidway.prompts import load_system_prompt, normalize_system_prompt, system_message


@pytest.mark.parametrize("name", ["main", "advisor", "review"])
def test_system_message_json_round_trip_preserves_real_newlines(name):
    prompt = load_system_prompt(name)
    message = system_message(name)
    restored = json.loads(json.dumps(message.model_dump(), ensure_ascii=False))
    assert restored["content"] == prompt
    assert "\n\n## " in restored["content"]
    assert "\\n" not in restored["content"]
    assert "\n- " in restored["content"]


def test_normalization_preserves_bullets_and_literal_escape_content():
    assert (
        normalize_system_prompt("  # Role\r\n\r\n  - First\r\n  - Second  ") == "# Role\n\n- First\n- Second"
    )
    assert normalize_system_prompt(r"A literal \n example") == r"A literal \n example"
    with pytest.raises(ValueError):
        normalize_system_prompt("  \r\n ")
    with pytest.raises(ValueError):
        load_system_prompt("../../outside")
