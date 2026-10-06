FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=America/Bogota

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[dev]"

COPY alembic.ini ./
COPY alembic ./alembic
COPY tests ./tests

# Usuario sin privilegios
RUN useradd --create-home appuser && mkdir -p /app/data/cache && chown -R appuser /app
USER appuser

CMD ["sports-analytics", "--help"]
