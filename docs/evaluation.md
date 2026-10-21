# Evaluation summary

This summary describes recorded verification from **21 October 2026**. It preserves unsuccessful results and separates deterministic checks from model judgments. The [eight assessment answers](assessment-answers.md) are actual outputs from those runs. Documentation cleanup did not rerun the application benchmarks.

## Method and recorded configuration

The benchmark contains 60 single-turn cases (40 development and 20 historically held-out), 12 two-turn conversations (8 development and 4 historically held-out), and eight separate judge calibration fixtures. Coverage includes prices and qualifiers, programme inclusions and exclusions, comparisons, follow-up context, missing information, unsupported actions, medical advice, false premises, injection, and English, Spanish and Arabic responses.

Labels are **AI-curated and source-verified**, bound to frozen source hashes. Dataset revision 3 uses corpus `3af52f5f-7224-40fb-9768-e96d381a669f`, extractor revision 4 and index `minilm-220-table-rows-v2`. The corpus contains seven sources, 255 evidence sections, 276 chunks and 59 structured price records, including five variable-cost records. Evaluation stops when content or extractor versions no longer match the labels.

| Component | Recorded configuration |
| --- | --- |
| Generation and support verification | Bedrock GPT-OSS 120B, `openai.gpt-oss-120b-1:0` |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2`, local CPU |
| Answer retrieval | Hybrid dense/BM25 reciprocal rank fusion, constant 60 |
| Offline judge | `inception/mercury-decide:free`, OpenRouter typed `/systemone` API |
| Judge threshold | Fixed at 0.8; 16/16 calibration criterion decisions agreed with fixture labels |

Deterministic checks cover outcomes, exact decimal prices, currency, qualifiers, treatment/programme associations, required price records and citation identity/evidence. A deterministic failure fails the case regardless of judge scores. DeepEval custom metrics separately judge grounding, correctness/completeness, scope/safety and language; a conversation metric assesses the turn sequence. The decision endpoint returns numerical decisions without textual explanations. The offline judge is not involved in serving chat answers.

## Recorded results

| Check | Result |
| --- | --- |
| Single-turn cases passing all deterministic and judge gates | **58/60** |
| Single-turn deterministic gates | **60/60** |
| Conversations passing all deterministic and judge gates | **10/12** |
| Conversation deterministic gates | **24/24 turns** |
| Operational errors in the final answer suites | **0** |
| Single-turn application latency | Median **2.240 s**, p95 **3.684 s** |

No fabricated or misassociated prices were detected by the finite benchmark's checks. This does not establish universal reliability. Latency excludes offline judging and browser delivery and may change with model initialization, network conditions and traffic. The development single-turn split passed 39/40; the historically held-out split passed 19/20.

The earlier verification documentation also records 52 backend unit/PostgreSQL integration tests passing; Ruff lint and format passing; frontend typecheck and build passing; eight mocked browser tests, one mobile test and one real-provider Compose browser test passing. Compose checks covered seven-source ingestion, readiness, validated streaming, idempotency and completed-history recovery after API/web restart. Refresh checks covered unchanged-page reuse, changed-price publication, pinned-version reads, rollback and lock exclusion. The optional evaluation image built and completed a one-case provider/judge-backed run. These are historical checks, not newly executed checks for this documentation change.

## Remaining strict failures

| Case | Failed score | Interpretation retained from the source audit |
| --- | ---: | --- |
| q10: outpatient monitoring | Correctness 0.002473 | Completeness omission: laboratory work and tumour markers were listed, but the included prescribed imaging study was omitted. |
| q57: eligibility preparation | Correctness 0.201813 | Completeness disagreement: the answer lists records and a subsequent medical eligibility evaluation, but the judge rejected completeness against the labelled expectation. |
| c03: Turkey follow-up | Conversation 0.651355 | Suspected judge false negative: Mexico prices remain scoped correctly and the follow-up says equivalent Turkey information was not found, with a quote link. |
| c07: diabetes follow-up | Conversation 0.798187 | Suspected judge false negative: the answer retains context and refers individual suitability to the doctor/clinic without making a recommendation. |

These interpretations are audit observations, not explanations supplied by the judge. All four remain failures at the unchanged threshold.

## Retrieval comparison

Dense, actual `rank_bm25` and hybrid retrieval were compared on the same 29 answerable development queries, frozen corpus, chunking and context budgets. The protocol uses identical English questions and author-labelled treatment/intent inputs, with structured price lookup enabled in every mode; retained price candidate sets are identical. Text hits map to stable parent evidence, so duplicate chunks earn no extra gain. Unanswerable questions are excluded from recall denominators and tested separately for fallback behaviour.

| Mode | Recall@3 | nDCG@3 | MRR@3 | Recall@5 | nDCG@5 | MRR@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| MiniLM dense | 0.851 | 0.796 | 0.787 | 0.931 | 0.831 | 0.803 |
| BM25 | 0.793 | 0.665 | 0.621 | 0.908 | 0.717 | 0.655 |
| Hybrid RRF | 0.874 | 0.817 | 0.805 | 0.966 | 0.860 | 0.822 |

Hybrid became the default because development nDCG@5 improved without reducing Recall@5, and the final deterministic price gates passed. This small curated comparison does not establish statistical superiority on future traffic or isolate the effects of the question resolver.

## Evidence quality and limitations

All current cases have previously been inspected, and initial held-out failures informed implementation fixes. The family-disjoint split is therefore regression coverage, not an independent holdout. Revision 3 expanded acceptable citation evidence for the 24/7-supervision fact after a false negative exposed an overly narrow label; the required fact, expected outcome and threshold were unchanged. Ten improvement rounds are retained in a ledger reconstructed from earlier reports; later runs are verification. Failures were retained and thresholds were not lowered.

Source extraction excludes unnamed price rows and leaves ambiguous-currency amounts as text. Category estimates retain their scope and variable costs remain unknown. Video evidence contains visible listings only. These constraints reduce unsupported claims but do not establish the accuracy or clinical validity of the website itself.

A concurrent evaluation-image build and live inference exhausted the 2 GB test VM and restarted the API. Later quiescent browser and restart checks passed. Deployment guidance now calls for at least 4 GB RAM and building before reviewer traffic. Local Compose checks do not verify a public HTTPS URL or the assessment's seven-day availability requirement. Further limitations and priorities are in the [half-page write-up](limitations.md).

## Reproduce the checks

For host-based evaluation, follow the [README setup](../README.md), install `uv sync --extra eval` in `apps/agent/`, and configure a reachable PostgreSQL/pgvector database plus generation and judge credentials. Use the audited corpus matching the labels; if sources changed, audit the snapshots and labels before regenerating bindings.

From `apps/agent/`, opt into database integration tests:

```sh
RUN_DB_TESTS=1 uv run pytest tests/test_api_integration.py tests/test_refresh_integration.py -q
```

The database test account needs permission to create disposable test databases. Normal `uv run pytest -q` skips these tests and live evaluations unless opted in.

For retrieval and provider-backed evaluation:

```sh
uv run placidway compare-retrieval
uv run placidway calibrate-judge
uv run placidway evaluate --split dev --mode hybrid
uv run placidway evaluate-conversations --split dev --mode hybrid
RUN_LIVE_EVALS=1 EVAL_SPLIT=all RETRIEVAL_MODE=hybrid uv run deepeval test run tests/evals/test_single_turn.py
RUN_LIVE_EVALS=1 EVAL_SPLIT=all RETRIEVAL_MODE=hybrid uv run deepeval test run tests/evals/test_conversation.py
```

These commands use configured providers and may incur API costs. Calibration must match the judge model, rubric and fixed threshold. Run the native single-turn and conversation suites sequentially because concurrent DeepEval processes share temporary files. A retained strict failure produces a nonzero suite exit; numerical results still need inspection.

For the optional Compose evaluation image, from the repository root:

```sh
docker compose --profile eval run --build --rm evals evaluate --split dev --mode hybrid
```

Reports are stored locally under ignored `reports/`; curated JSON exports and the original detailed evaluation audit are retained locally under ignored `docs/results/`. Internal specifications and operational details are archived under ignored `docs/contract/`. The submission documents contain the results needed for review without requiring those archives. No reports are uploaded to LangSmith or Confident AI.
