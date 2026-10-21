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

## Deployment verification

Release `v1.0.0` was built by the tag-triggered [GHCR publishing workflow](https://github.com/indrabayuu/placidway-assesment/actions/runs/39625510710) and deployed on an amd64 server at [http://43.173.33.81](http://43.173.33.81). Both image publishing jobs passed. A logical PostgreSQL restore retained the active Compose corpus `ad0defbc-d49d-4294-a553-1ed89220881d`, seven sources, 276 chunks and 59 price records. The stored-vector fingerprint matched the local database before and after testing, and the ingestion-run count stayed unchanged. Source snapshots and the local model cache were not transferred; the query model downloaded into a new server cache. Automatic refresh was not started.

| Live deployment check | Result |
| --- | --- |
| Public frontend, liveness and readiness endpoints | Passed |
| External PostgreSQL connection on port 5432 | Passed |
| Real-provider cost answer, citations, idempotency and validation before streaming | Passed |
| Persisted follow-up about missing recovery time | Passed; the answer distinguished recovery from programme duration and offered a quote |
| Owner/client history and stream isolation; owner-only refresh | Passed |
| Browser integration: Arabic RTL, citations, activity panel, history reopening and mobile layout | 1 passed |
| API/web restart and completed-history persistence | Passed |
| System service enabled for startup | Verified enabled and active; a full machine reboot was not tested |
| Conversation, session and checkpoint cleanup after testing | Verified empty on target |

An initial deployment smoke assertion expected the missing-recovery response's outcome to be exactly `unknown`; the observed answer was correctly worded but labeled `partial`. That ad hoc check was corrected to allow either outcome while requiring explicit missing-recovery wording and a quote/contact link. No application code, assessment labels, benchmark thresholds or historical scores changed. The full answer benchmark was not rerun for this deployment.

The server has 2 GB RAM and 2 GB swap; these checks ran without a concurrent image build or load test. This does not replace the 4 GB deployment recommendation. The public demo currently uses HTTP, and seven-day continuous availability has not yet elapsed. GHCR packages were initially private and deployed using temporary authenticated pulls; changing package visibility to Public is still a one-time GitHub settings step.

Release `v1.0.1` fixes message submission on public HTTP origins where `crypto.randomUUID()` is unavailable. The original live browser check used localhost through an SSH tunnel, which browsers treat as a secure context. A regression test now disables that API and verifies a valid version-4 message UUID; all eight browser smoke tests and the production build passed. A direct browser check at `http://43.173.33.81` then confirmed an insecure context, successful UUID submission and a sourced price answer without browser errors. Only that test's conversation was removed; existing user history, database credentials, corpus and document vectors were preserved.

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
