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

RUN pip install ".[server]"

RUN mkdir -p /data
ENV PULSE_DB_PATH=/data/pulse.sqlite

COPY docker/server-entrypoint.sh /usr/local/bin/pulse-server-entrypoint
RUN chmod +x /usr/local/bin/pulse-server-entrypoint

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/pulse-server-entrypoint"]
CMD []
