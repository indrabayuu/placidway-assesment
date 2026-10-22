# PlacidWay website knowledge chatbot

A multilingual chat assistant for seven approved PlacidWay pages. It answers with source citations, supports follow-ups, and preserves price context. This repository contains the agent-v2 refactor; it has **not** been deployed. The public demo still runs the previous release.

## Assessment

| Deliverable | File |
| --- | --- |
| Assessment answers | [docs/assessment-answers.md](docs/assessment-answers.md) |
| Evaluation method and results | [docs/evaluation.md](docs/evaluation.md) |
| Limitations and next steps | [docs/limitations.md](docs/limitations.md) |

The existing demo is [http://43.173.33.81](http://43.173.33.81). It uses HTTP. The reviewer login secret is stored in the deployment configuration; the required seven-day availability period has not been verified.

## Run locally

Requires Docker Compose, Python 3, network access to the approved sources and embedding/provider services, and about 4 GB RAM.

From the repository root:

```sh
python3 scripts/bootstrap_env.py
# Set provider and database values in .env.
docker compose config --quiet
docker compose up --build -d
```

Open [http://localhost:8080](http://localhost:8080) and sign in with `AUTH_OWNER_SECRET` or `AUTH_CLIENT_SECRET` from `.env`. Initial ingestion downloads the embedding model and may take several minutes. Check status with:

```sh
docker compose ps
docker compose logs --tail=60 api refresh
```

The bootstrap script creates missing login secrets and protects `.env` from Git. Accounts have separate histories; people sharing one secret share that account’s history.

| Setting | Use |
| --- | --- |
| `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_API_KEY` | Required OpenAI-compatible generation provider |
| `DATABASE_URL` | PostgreSQL with pgvector |
| `MAIN_REASONING_EFFORT=medium`, `HELPER_REASONING_EFFORT=high` | Main agent and helper model settings |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | Offline DeepEval judge only |
| `RETRIEVAL_MODE=hybrid` | Dense + BM25 retrieval; `dense` and `bm25` are available |
| `COOKIE_SECURE=true` | Required behind public HTTPS |

The credential-free [.env.example](.env.example) lists additional settings. Provider keys remain on the backend.

## Agent and data flow

The chat uses LangChain `create_agent` with its LangGraph runtime. The main agent uses medium reasoning effort and two tools:

- `knowledge_base_search` runs hybrid retrieval with English queries, up to three times per turn.
- `consult_evidence_advisor` offers an optional second opinion, at most once per turn.

The agent answers naturally in the user’s language. Its system prompt sets scope, source-use and medical boundaries. Three readable Markdown prompts live in `apps/agent/src/placidway/system_prompts/`. Conversation state holds `messages`, current `documents`, and `history_documents` cited in accepted answers.

The runtime guardrail checks citations and price associations, then uses a high-effort semantic reviewer. It permits one revision before a limitation response. Guardrails remain enabled by default. They can add latency or reject supported answers; the reviewer also missed an unsupported price qualifier in the recorded challenge. See [evaluation results](docs/evaluation.md).

The interface streams model thinking, operational updates and provisional answer text separately. A rejected answer is replaced; only the accepted canonical answer enters restored history. Provisional content can be seen before review.

The allowlist in [sources.py](apps/agent/src/placidway/sources.py) covers seven clinic, treatment, video-listing and price pages. Ingestion preserves source sections and structured `Decimal` price facts. Retrieval uses MiniLM embeddings, pgvector, BM25 and reciprocal-rank fusion; table rows retain their headers. Videos contribute visible listing text only.

## Refresh and deployment

The owner can refresh from the UI or run:

```sh
docker compose exec api uv run --no-sync placidway refresh
```

Refresh validates all seven sources before publishing a new corpus version. Failed refreshes preserve the active version, and each answer uses one pinned version.

For hosting, use a persistent Linux server, HTTPS, a protected `.env`, and long-lived SSE support. Set `COOKIE_SECURE=true`, protect the PostgreSQL port, and run one API worker. The readiness endpoint checks configuration and corpus availability, not the generation provider.

The tag-triggered GitHub Actions workflow publishes agent and web images to GHCR. Tag a commit on `main` as `vX.Y.Z`, then deploy matching images with [compose.deploy.yaml](compose.deploy.yaml) and `IMAGE_TAG`. After the first publication, make both GHCR packages public if the server will pull without registry credentials. The current server runs release `v1.0.2`; the refactor in this repository has not been deployed.

PostgreSQL volumes hold corpus, conversation and checkpoint data. Back up the database and source snapshots; avoid `docker compose down -v` if retaining data. The deployment Compose file keeps scheduled refresh behind its `refresh` profile.

## Development

Host development needs Python 3.12, uv, Bun, and a reachable PostgreSQL/pgvector database. From `apps/agent/`:

```sh
uv sync --extra eval
uv run placidway migrate
uv run placidway refresh
uv run placidway serve --host 127.0.0.1
```

In another terminal:

```sh
cd apps/web
bun install --frozen-lockfile
bun run dev
```

Vite serves port 5173 and proxies to the API on port 8000. See [docs/evaluation.md](docs/evaluation.md) for current results and the DeepEval gate. The gate currently fails; historical scores for the previous workflow do not establish readiness.
