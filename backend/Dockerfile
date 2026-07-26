ARG PYTHON_IMAGE=python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
FROM ${PYTHON_IMAGE} AS runtime

ARG FFMPEG_PACKAGE_VERSION=7:7.1.5-0+deb13u1

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends "ffmpeg=${FFMPEG_PACKAGE_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

RUN uv sync --frozen --no-dev

COPY assets ./assets
COPY app ./app
COPY scripts ./scripts

ENV PATH="/opt/venv/bin:${PATH}"
