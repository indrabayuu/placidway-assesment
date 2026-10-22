# Evaluation

## Current refactor

The full runtime-guarded DeepEval run failed. The gate keeps its 0.8 per-metric threshold and requires every case to pass.

| Suite | Passed |
| --- | ---: |
| Single-turn | 51/60 |
| Conversations | 6/12 |
| Automatic language | 8/11 |
| Refactor behavior and guardrail tests | 7/8 |

The cases ran with no skips. The reviewer rejected some answerable questions or could not produce an acceptable fallback. It also accepted a claim that a starting price was a “guaranteed final total.” Other failures include missing details, price/source mismatches, language errors, and strict judge disagreements. The refactor is not release-ready.

The source-bound set contains 60 single-turn cases, 12 conversations, seven price-language variants, four conversation language-history checks, and eight refactor-specific checks. Labels were AI-curated and source-verified. Cases already inspected during development count as regression coverage, not an independent holdout. A fresh, independently reviewed holdout remains outstanding.

The guarded run used the audited seven-source corpus, hybrid retrieval, GPT-OSS 120B (main effort medium; advisor/reviewer high), and the calibrated Mercury Decide offline judge. Its report is under ignored `reports/gates/20261022T152036Z/`. Runtime review is separate from DeepEval; it is not the offline judge.

## Runtime-check ablation

A separate run bypassed the deterministic runtime checks, semantic reviewer, revision and fallback. Prompt instructions for source use, scope, language, and medical safety remained. Search and consultation limits remained. The same offline checks still scored completed answers. The reviewer-specific challenge tests were excluded because the reviewer was disabled.

| Suite | Guarded | Checks bypassed |
| --- | ---: | ---: |
| Single-turn | 51/60 | 47/60 |
| Conversations | 6/12 | 6/12 |
| Language | 8/11 | 9/11 |
| Total across these suites | 65/83 | 62/83 |

Across 58 single-turn cases with scores in both runs, average DeepEval scores rose for grounding (0.966 to 0.994), correctness/completeness (0.935 to 0.962), and scope/safety (0.963 to 0.984), but language fell (1.000 to 0.965). Conversation pass count stayed the same. Some runtime fallbacks disappeared; unchecked answers still included wrong citations, missing evidence, and language/scope errors.

Single-turn median/p95 latency fell from 6.12/23.43 seconds guarded to 2.07/3.46 seconds without checks. Average model calls fell from 3.17 to 1.87. This is one sequential run per case, not a production estimate. The aggregate pass count fell by three, so the ablation does not support removing runtime guardrails.

Detailed answer-level records are in the Git-ignored [.local-reports ablation summary](../.local-reports/guardrail-ablation-20261022T155303Z.md) and [JSON results](../.local-reports/guardrail-ablation-20261022T155303Z.json). The normal application keeps runtime guardrails enabled.

## Previous workflow

The earlier structured workflow scored 58/60 single-turn cases and 10/12 conversations at the same 0.8 threshold. Those scores are historical and not directly comparable to the refactor: the answer pipeline, guardrails, and evaluation adapter changed. Its historically held-out split was later used for development, so it is not an untouched holdout.

## Method and reproducibility

The offline evaluator checks evidence grounding, correctness/completeness, scope/safety, language, price records and qualifiers, citation identity, and conversation behavior. Deterministic checks can fail a case even when semantic judge scores pass. The dataset and judge calibration must match the frozen source hashes, corpus and rubric. No score establishes clinical validity or universal reliability.

To run the mandatory refactor gate, configure the audited PostgreSQL corpus and generation/judge credentials, then run from `apps/agent/`:

```sh
uv run python -m placidway.evaluation.gate
```

The gate executes single-turn, conversation, automatic-language, and refactor suites sequentially. It fails on missing cases, skips, errors, metric failures, or changed prompts/code. Reports are local under ignored `reports/gates/`; evaluation calls may incur provider charges.
