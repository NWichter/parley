FROM ghcr.io/astral-sh/uv:python3.15-trixie-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY audio/voices.json ./app/replays/data/voices.json

FROM python:3.15-slim-trixie
WORKDIR /app
RUN useradd -r -u 1001 app && mkdir -p /data/replays && chown -R app:app /data
ENV PATH="/app/.venv/bin:$PATH"
COPY --from=build --chown=app:app /app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
