"""Language-policy variants of the existing source-verified cost golden."""

import os

import pytest
from deepeval import assert_test

from placidway.config import settings
from placidway.evaluation.judge import DecisionJudge
from placidway.evaluation.metrics import make_metrics
from placidway.evaluation.runner import evaluate_case, load_dataset, resources
from placidway.evaluation.runner import test_case as to_test_case
from placidway.llm import Generator
from placidway.models import ResolvedRequest
from placidway.prompts import RESOLVE

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_EVALS") != "1", reason="Explicit live evaluation opt-in required"
    ),
]

seed = next(row for row in load_dataset()["cases"] if row["id"] == "q01")
variants = [
    ("indonesian", "Berapa biaya program rawat jalan kanker adenokarsinoma di Meksiko?", "id"),
    ("japanese", "メキシコの腺がん外来治療プログラムの費用はいくらですか？", "ja"),
    ("french", "Combien coûte le programme ambulatoire du cancer adénocarcinome au Mexique ?", "fr"),
    ("hindi", "मेक्सिको में एडेनोकार्सिनोमा के बाह्य रोगी उपचार कार्यक्रम की लागत कितनी है?", "hi"),
    ("hebrew", "כמה עולה תוכנית טיפול חוץ לאדנוקרצינומה במקסיקו?", "he"),
    (
        "explicit-english",
        "Berapa biaya program rawat jalan kanker adenokarsinoma di Meksiko? Answer in English.",
        "en",
    ),
    (
        "explicit-spanish",
        "How much does outpatient adenocarcinoma treatment in Mexico cost? Responde en español.",
        "es",
    ),
]


@pytest.mark.parametrize("name,question,language", variants, ids=[row[0] for row in variants])
async def test_automatic_language(name, question, language, eval_capture):
    case = {**seed, "id": "language-" + name, "question": question, "response_language": language}
    config, db, _, workflow = await resources(os.environ.get("RETRIEVAL_MODE", "hybrid"))
    try:
        result = await evaluate_case(workflow, case, DecisionJudge(config))
        eval_capture(result, corpus=workflow.pinned_version)
        assert result["answer"]["response_language"] == language
        assert result["answer"]["diagnostics"]["request"]["query"].isascii()
        assert all(value for key, value in result["checks"].items() if key != "missing_price_ids")
        assert_test(to_test_case(case, result["answer"]), make_metrics(DecisionJudge(config)))
    finally:
        await db.close()


@pytest.mark.parametrize(
    "question,expected",
    [
        ("OK?", "fr"),
        ("And the inpatient price?", "en"),
        ("Et le programme hospitalier ?", "fr"),
        ("Et le programme hospitalier ? 日本語で答えてください。", "ja"),
    ],
)
async def test_language_choice_with_conversation_history(question, expected):
    # Exercise the real resolver with an existing language preference and a new turn.
    request = await Generator(settings()).call(
        ResolvedRequest,
        RESOLVE,
        {
            "question": question,
            "history": [
                {"role": "user", "content": variants[2][1]},
                {"role": "assistant", "content": "Le prix commence à USD 18,995.", "response_language": "fr"},
            ],
        },
        [1],
    )
    assert request.response_language == expected
    assert request.query.isascii()
