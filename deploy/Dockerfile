# The live demo, one port, no build step at the far end.
#
#   docker build -t sarathi -f deploy/Dockerfile .
#   docker run --rm -p 8420:8420 sarathi
#
# Multi-arch by construction: the base image and uv both have arm64 builds, so
# this is the same file on an Ampere A1 free-tier VM as on an x86 laptop.
FROM python:3.11-slim

# uv resolves and installs from the committed lockfile, so the image gets the
# same versions the benchmark numbers were measured against.
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

# The unprivileged user comes first and owns everything from the start. Doing it
# the other way round — build as root, `chown -R` at the end — rewrites every
# file in the virtualenv into a fresh layer, and numpy and scipy are large
# enough that it cost 184 MB of duplicate image for nothing.
RUN useradd --create-home --uid 10001 sarathi
WORKDIR /app
RUN chown sarathi:sarathi /app
USER sarathi

# Dependencies first: they change far less often than the stack does, so an
# edit to the planner does not re-resolve numpy and scipy.
COPY --chown=sarathi:sarathi pyproject.toml uv.lock README.md ./
RUN mkdir -p sarathi && touch sarathi/__init__.py \
 && uv sync --locked --no-dev --no-install-project

COPY --chown=sarathi:sarathi sarathi/ ./sarathi/
COPY --chown=sarathi:sarathi scenarios/ ./scenarios/
COPY --chown=sarathi:sarathi scripts/ ./scripts/
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SARATHI_PORT=8420 \
    SARATHI_SCENARIO=village_road_unmarked

EXPOSE 8420

# /healthz answers without opening a WebSocket, so the check does not start a
# simulation and pin a core every thirty seconds.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"SARATHI_PORT\"]}/healthz').read()"

# 0.0.0.0 inside the container only: what the outside world can reach is
# whatever you published with -p, or the reverse proxy in front of it.
CMD ["sh", "-c", "exec sarathi serve --host 0.0.0.0 --port $SARATHI_PORT --scenario $SARATHI_SCENARIO"]
