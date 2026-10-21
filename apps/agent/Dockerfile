FROM ghcr.io/astral-sh/uv:0.11.22 AS uv
FROM python:3.12-slim AS base
COPY --from=uv /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
ENV UV_LINK_MODE=copy UV_NO_CACHE=1 UV_COMPILE_BYTECODE=1 HF_HOME=/cache/huggingface LANGSMITH_TRACING=false DEEPEVAL_TELEMETRY_OPT_OUT=YES
WORKDIR /app/agent
COPY pyproject.toml uv.lock ./

FROM base AS runtime
RUN uv sync --frozen --no-install-project --no-dev
COPY src ./src
RUN uv sync --frozen --no-dev
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app && mkdir -p /app/data /app/reports /cache/huggingface && chown -R app:app /app/data /app/reports /cache
USER app
ENTRYPOINT ["uv", "run", "--no-sync", "placidway"]
CMD ["serve"]

FROM base AS eval
RUN uv sync --frozen --extra eval --no-install-project
COPY src ./src
COPY tests ./tests
RUN uv sync --frozen --extra eval
ENTRYPOINT ["uv", "run", "--no-sync", "placidway"]
CMD ["evaluate"]
