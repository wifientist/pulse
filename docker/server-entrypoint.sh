#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$(dirname "${PULSE_DB_PATH:-/data/pulse.sqlite}")"

echo "[pulse] running migrations"
alembic upgrade head

echo "[pulse] starting server"
exec pulse-server
