import asyncio
import math
import os

# Must precede DeepEval imports; all diagnostics/reports stay local.
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"

from deepeval.metrics import BaseConversationalMetric, BaseMetric

from placidway.evaluation.judge import CONVERSATION_QUESTIONS, QUESTIONS


def retrieval_metrics(retrieved_ids, relevant_ids, k=5, grades=None, required_groups=None):
    ranked = list(retrieved_ids)[:k]
    relevant = set(relevant_ids)
    if not relevant:
        return {"recall": None, "ndcg": None, "mrr": None, "essential_coverage": None}
    grades = grades or {key: 1 for key in relevant}
    seen = set()
    dcg = 0.0
    for rank, key in enumerate(ranked):
        if key not in seen:
            dcg += (2 ** grades.get(key, 0) - 1) / math.log2(rank + 2)
            seen.add(key)
    ideal = sum(
        (2**grade - 1) / math.log2(rank + 2)
        for rank, grade in enumerate(sorted(grades.values(), reverse=True)[:k])
    )
    groups = required_groups or [[key] for key in relevant]
    return {
        "recall": len(set(ranked) & relevant) / len(relevant),
        "ndcg": dcg / ideal if ideal else 0,
        "mrr": next((1 / (rank + 1) for rank, key in enumerate(ranked) if key in relevant), 0),
        "essential_coverage": sum(bool(set(group) & set(ranked)) for group in groups) / len(groups),
    }


def judge_state(test_case):
    metadata = test_case.metadata or {}
    return {
        "question": test_case.input,
        "actual_output": test_case.actual_output,
        "retrieved_evidence": test_case.retrieval_context or [],
        "price_facts": metadata.get("price_facts", []),
        "required_facts": metadata.get("required_facts", []),
        "prohibitions": metadata.get("prohibitions", []),
        "expected_outcomes": metadata.get("expected_outcomes", []),
        "response_language": metadata.get("response_language", "en"),
    }


class DecisionMetric(BaseMetric):
    def __init__(self, judge, criterion, threshold=0.8):
        self.judge, self.criterion, self.threshold = judge, criterion, threshold
        self.async_mode = True
        self.strict_mode = False
        self.include_reason = True
        self.score = 0.0
        self.error = None
        self.reason = ""
        self.evaluation_model = judge.config.openrouter_model

    @property
    def __name__(self):
        return "Decision " + self.criterion.replace("_", " ").title()

    def measure(self, test_case, *args, **kwargs):
        return asyncio.run(self.a_measure(test_case))

    async def a_measure(self, test_case, *args, **kwargs):
        result = await self.judge.decide(judge_state(test_case), QUESTIONS)
        self.score = float(result["answers"][self.criterion]["noul"])
        self.success = self.score >= self.threshold
        self.reason = f"Typed probability for criterion '{self.criterion}': {self.score:.4f}. See saved evidence and rubric; the decision endpoint does not generate explanations."
        return self.score

    def is_successful(self):
        return self.error is None and self.score >= self.threshold


class ConversationDecisionMetric(BaseConversationalMetric):
    def __init__(self, judge, threshold=0.8):
        self.judge, self.threshold = judge, threshold
        self.async_mode = True
        self.strict_mode = False
        self.include_reason = True
        self.score = 0.0
        self.error = None
        self.reason = ""
        self.evaluation_model = judge.config.openrouter_model

    @property
    def __name__(self):
        return "Decision Conversation Correctness"

    def measure(self, test_case, *args, **kwargs):
        return asyncio.run(self.a_measure(test_case))

    async def a_measure(self, test_case, *args, **kwargs):
        state = {
            "turns": [turn.model_dump(mode="json") for turn in test_case.turns],
            "expectations": {
                key: value
                for key, value in (test_case.metadata or {}).items()
                if key in ("expected_turns", "languages", "price_facts_by_turn", "turn_checks")
            },
        }
        result = await self.judge.decide(state, CONVERSATION_QUESTIONS)
        self.raw_judgment = result
        self.score = float(result["answers"]["complete"]["noul"])
        self.success = self.score >= self.threshold
        self.reason = f"Conversation rubric probability: {self.score:.4f}; see local turn evidence."
        return self.score

    def is_successful(self):
        return self.error is None and self.score >= self.threshold


def make_metrics(judge, thresholds=None):
    thresholds = thresholds or {}
    return [DecisionMetric(judge, criterion, thresholds.get(criterion, 0.8)) for criterion in QUESTIONS]


class GuardrailDecisionMetric(BaseMetric):
    """Check a live review verdict against a source-audited challenge label."""

    threshold = 0.8
    async_mode = True
    strict_mode = True
    include_reason = True

    @property
    def __name__(self):
        return "Guardrail Challenge Decision"

    def measure(self, test_case, *args, **kwargs):
        self.score = float(test_case.actual_output == test_case.expected_output)
        self.success = self.score >= self.threshold
        self.reason = "Live review verdict compared with the source-bound expected decision."
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case)

    def is_successful(self):
        return self.score >= self.threshold
