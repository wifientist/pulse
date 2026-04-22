FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iperf3 \
        iputils-ping \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY shared ./shared
COPY agent ./agent

RUN pip install ".[agent]"

RUN mkdir -p /var/lib/pulse
ENV PULSE_TOKEN_FILE=/var/lib/pulse/agent.token

ENTRYPOINT ["/usr/bin/tini", "--", "pulse-agent"]
