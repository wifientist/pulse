# --- Stage 1: build the React/Vite SPA -------------------------------------
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- Stage 2: the FastAPI server, with SPA assets baked in ----------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini ./
COPY shared ./shared
COPY server ./server
COPY agent ./agent

# Editable install: imports resolve to the live source tree under /app (not copies in
# site-packages). Hot-swap iteration (`docker cp file pulse-server:/app/...`) affects
# the running Python process on restart — no image rebuild.
RUN pip install -e ".[server]"

# Pre-build wheels for agent-side deps (incl. the PEP 517 build backend that pip needs
# to install `-e .[agent]` from source). Target agents with no Internet access use
# `pip install --no-index --find-links=<dir>` pointed at this directory and never talk
# to pypi. The wheels land inside the source tarball so a single file is all that needs
# to be shipped to a target.
RUN mkdir /app/agent-wheels \
    && pip wheel --wheel-dir=/app/agent-wheels hatchling editables \
    && pip wheel --wheel-dir=/app/agent-wheels ".[agent]"

# Bake the agent source tarball into the image so the self-upgrade endpoint can serve
# it without any out-of-band file management. The tarball has `pulse/` as its top-level
# directory so the existing install-agent.sh's `tar xzf --strip-components=1` path
# continues to work unchanged.
RUN cd / && tar czf /app/agent-source.tar.gz \
    --exclude='__pycache__' --exclude='*.pyc' \
    --transform='s|^app|pulse|' \
    app/pyproject.toml app/README.md app/alembic.ini \
    app/shared app/server app/agent app/agent-wheels

# SPA assets from the node stage. The server mounts StaticFiles when
# PULSE_WEB_DIST_DIR points at an existing directory.
COPY --from=web /web/dist ./web/dist
ENV PULSE_WEB_DIST_DIR=/app/web/dist

RUN mkdir -p /data
ENV PULSE_DB_PATH=/data/pulse.sqlite

COPY docker/server-entrypoint.sh /usr/local/bin/pulse-server-entrypoint
RUN chmod +x /usr/local/bin/pulse-server-entrypoint

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/pulse-server-entrypoint"]
CMD []
