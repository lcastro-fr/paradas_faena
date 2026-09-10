FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV UV_CACHE_DIR=/tmp/uv-cache
ENV UV_NO_SYNC=1
ENV PATH="/opt/venv/bin:$PATH"
ENV HOME=/tmp

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY src/ ./src/
RUN uv sync --locked && rm -rf /tmp/uv-cache



RUN useradd --system --uid 10001 --no-create-home faena
USER faena

COPY migrations/ ./migrations/
COPY queries/ ./queries/

ENTRYPOINT ["uv", "run", "paradas-faena"]
