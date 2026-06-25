FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN uv sync --frozen --no-dev

COPY assets ./assets
COPY app ./app

ENV PATH="/opt/venv/bin:${PATH}"
