# Image de l'assistant. Le corpus (build/) est produit par la CI avant la
# construction : une image porte une version de corpus, une revision aussi.

FROM python:3.13-slim AS construction
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra gcp --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra gcp

FROM python:3.13-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=construction /app/.venv ./.venv
COPY src ./src
COPY config ./config
COPY prompts ./prompts
COPY build/corpus.jsonl build/coordonnees.json ./build/
ARG CORPUS_VERSION=local
ENV ASSISTANT_CORPUS_VERSION=${CORPUS_VERSION} \
    ASSISTANT_RACINE=/app \
    PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
# --no-access-log : le journal d'acces d'uvicorn ecrirait l'adresse du client.
CMD ["uvicorn", "assistant.api:app_depuis_env", "--factory", "--host", "0.0.0.0", \
     "--port", "8080", "--no-access-log", "--timeout-graceful-shutdown", "10"]
