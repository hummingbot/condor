# The collector image.
#
# It builds from the Condor repository root rather than from this directory,
# because ingest.py imports `condor.telemetry.schema` — the same taxonomy the
# client sanitises with — and that import is the whole reason the collector
# lives in this repo (FEAT-024). The cost is an image that carries Condor's
# dependency set rather than four packages; the benefit is that the emitter and
# the validator cannot drift apart. That trade was made deliberately.
#
# Build from the repo root:  docker build -f telemetry_server/Dockerfile .

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first so a code change does not re-resolve the world.
COPY pyproject.toml uv.lock* README.md ./
RUN uv sync --extra telemetry-server --no-dev --no-install-project

COPY condor/ ./condor/
COPY telemetry_server/ ./telemetry_server/
COPY utils/ ./utils/
COPY config_manager.py ./

# Nothing here needs to be root, and an unauthenticated public endpoint is the
# last process that should be.
RUN useradd --system --uid 10001 collector && chown -R collector /app
USER collector

EXPOSE 8000

# Plain HTTP on purpose: TLS terminates at the reverse proxy in front, matching
# how the hummingbot/deploy stack is already run.
CMD ["uv", "run", "--no-sync", "uvicorn", "telemetry_server.app:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000"]
