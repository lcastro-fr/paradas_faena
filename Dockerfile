FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

RUN useradd --system --uid 10001 --no-create-home faena
USER faena

COPY migrations/ ./migrations/
COPY queries/ ./queries/

ENTRYPOINT ["python", "-m", "paradas_faena"]
