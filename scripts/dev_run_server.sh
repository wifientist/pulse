#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

export PULSE_DB_PATH="${PULSE_DB_PATH:-./pulse.sqlite}"
export PULSE_ADMIN_TOKEN="${PULSE_ADMIN_TOKEN:-dev-admin-token}"
export PULSE_BIND_HOST="${PULSE_BIND_HOST:-127.0.0.1}"
export PULSE_BIND_PORT="${PULSE_BIND_PORT:-8080}"

alembic upgrade head
exec pulse-server
