import json

import httpx
import pytest
from test_evidence import evidence, fact

from placidway.config import Settings
from placidway.evaluation.judge import DecisionJudge, retry_delay
from placidway.evaluation.provenance import record_round, require_calibration
from placidway.evaluation.runner import deterministic_checks, load_dataset
from placidway.models import AnswerBlock, AnswerDraft, ResolvedRequest
from placidway.workflow import render


def test_dataset_has_metadata_and_disjoint_paraphrase_families():
    dataset = load_dataset()
    assert len(dataset["cases"]) == 60 and len(dataset["conversations"]) == 12
    assert sum(row["split"] == "dev" for row in dataset["cases"]) == 40
    assert sum(row["split"] == "dev" for row in dataset["conversations"]) == 8
    seen = {}
    for row in dataset["cases"]:
        assert row["corpus_version"] and row["annotation_rationale"]
        assert seen.setdefault(row["family_id"], row["split"]) == row["split"]
        assert all(key in row["expected_price_facts"] for key in row["required_price_ids"])
    for row in dataset["conversations"]:
        for family in row["family_ids"]:
            assert seen.setdefault(family, row["split"]) == row["split"]


def test_price_gate_checks_values_and_citation_identity_not_just_ids():
    price = fact()
    section = evidence()
    draft = AnswerDraft(outcome="answered", blocks=[AnswerBlock(kind="price", price_id=price.id)])
    text, citations = render(draft, [section], [price], "en")
    case = {
        "question": "Price?",
        "required_price_ids": [price.id],
        "expected_price_facts": {price.id: {"amount": "18995"}},
        "expected_outcomes": ["answered"],
        "relevant_evidence_ids": [section.id],
        "essential_groups": [[section.id]],
    }
    answer = {
        "outcome": "answered",
        "content": text,
        "response_language": "en",
        "citations": citations,
        "diagnostics": {
            "draft": draft.model_dump(),
            "request": ResolvedRequest(query="Price").model_dump(),
            "prices": [price.model_dump(mode="json")],
            "evidence": [section.model_dump()],
            "used_price_ids": [price.id],
        },
    }
    assert all(
        value for key, value in deterministic_checks(case, answer).items() if key != "missing_price_ids"
    )
    answer["content"] = text + " Another treatment costs USD 5."
    assert not deterministic_checks(case, answer)["price_values_context_and_qualifiers"]
    answer["diagnostics"]["used_price_ids"] = []
    assert not deterministic_checks(case, answer)["price_values_context_and_qualifiers"]
    answer["diagnostics"]["used_price_ids"] = [price.id]
    answer["content"] = text.replace("18,995", "50")
    assert not deterministic_checks(case, answer)["price_values_context_and_qualifiers"]
    answer["diagnostics"]["prices"][0]["amount"] = "50"
    assert not deterministic_checks(case, answer)["labelled_price_values"]
    answer["citations"][0]["url"] = "https://evil.example"
    assert not deterministic_checks(case, answer)["citation_allowlist_and_identity"]


def test_improvement_round_cap_and_calibration_are_enforced(tmp_path):
    cfg = Settings(_env_file=None, reports_dir=tmp_path)
    with pytest.raises(ValueError, match="maximum"):
        record_round(cfg, 11, "failure", "change")
    with pytest.raises(ValueError, match="already recorded"):
        record_round(cfg, 1, "failure", "change")
    with pytest.raises(RuntimeError, match="calibrate"):
        require_calibration(cfg)


@pytest.mark.parametrize(
    "answer",
    [None, [], {"type": "noul", "noul": True}, {"type": "noul", "noul": 1.1}, {"type": "score", "noul": 0.9}],
)
async def test_decision_adapter_rejects_malformed_typed_answers(monkeypatch, tmp_path, answer):
    original = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json={"answers": {"ok": answer}})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )
    cfg = Settings(_env_file=None, openrouter_api_key="fake-local-test-key", reports_dir=tmp_path)
    with pytest.raises(ValueError):
        await DecisionJudge(cfg).decide({}, {"ok": {"type": "noul", "instructions": "Check"}})


async def test_judge_cache_is_sensitive_to_rubric_and_answer(monkeypatch, tmp_path):
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert str(request.url).endswith("/systemone")
        return httpx.Response(
            200, json={"answers": {key: {"type": "noul", "noul": 0.95} for key in payload["questions"]}}
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )
    cfg = Settings(_env_file=None, openrouter_api_key="fake-local-test-key", reports_dir=tmp_path)
    judge = DecisionJudge(cfg)
    questions = {"ok": {"type": "noul", "instructions": "Check"}}
    await judge.decide({"answer": "A"}, questions)
    await judge.decide({"answer": "A"}, questions)
    await judge.decide({"answer": "B"}, questions)
    await judge.decide({"answer": "B"}, {"ok": {"type": "noul", "instructions": "Changed rubric"}})
    assert len(calls) == 3


def test_retry_after_supports_fractional_seconds_and_http_dates():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    assert retry_delay("2.5", 0) == 2.5
    assert retry_delay("600", 0) == 60
    assert 1 <= retry_delay(format_datetime(datetime.now(timezone.utc) + timedelta(seconds=3)), 0) <= 3
    assert retry_delay("invalid", 1) == 4
