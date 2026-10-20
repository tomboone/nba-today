# syntax=docker/dockerfile:1

FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.3 /uv /bin/uv

# Build against the base image's interpreter so the venv is valid in the runtime stage.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so source edits don't invalidate this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim

RUN groupadd --system app \
    && useradd --system --gid app --no-create-home app \
    && mkdir /data \
    && chown app:app /data

WORKDIR /app

COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/src ./src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

USER app

VOLUME /data

# `health` fails once the state file is over 26 hours old (spec §3.4); the start period covers the
# first plan at boot, which creates the file on a fresh volume.
HEALTHCHECK --interval=5m --start-period=1m CMD ["nba-today", "health"]

CMD ["nba-today", "run"]
