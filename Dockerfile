# Multi-stage: build deps once with uv, then run on a slim image.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1

# Install dependencies first (cached layer) using lockfile only.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Then copy the app and install it.
COPY main.py ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
# JSONL files live in the current working directory, which is expected to be
# bind-mounted from the host (see README). Run as the host user via
# `docker run --user "$(id -u):$(id -g)"` so written files are owned by you.
WORKDIR /work

ENTRYPOINT ["python", "/app/main.py"]
