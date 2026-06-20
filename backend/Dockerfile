FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

COPY app ./app

ENV PATH="/opt/venv/bin:${PATH}"
