#!/usr/bin/env bash
# Install the Pulse agent on a Linux host (no Docker).
#
# Usage (on the target host, as root or via sudo):
#
#   sudo PULSE_SERVER_URL=http://10.0.99.101:8080 \
#        PULSE_ENROLLMENT_TOKEN=<token> \
#        PULSE_HOSTNAME=<this-hostname> \
#        PULSE_REPORTED_IP=<test-subnet-ip> \
#        ./install-agent.sh
#
# Expects `pulse-source.tar.gz` (a tarball of the repo) to be in the current
# directory OR at $PULSE_SOURCE_TAR.
#
# What it does:
#   1. apt-get installs python3-venv, iputils-ping, iperf3
#   2. Extracts the source tarball into /opt/pulse/src
#   3. Creates a venv at /opt/pulse/.venv and `pip install`s the [agent] extras
#   4. Writes /etc/systemd/system/pulse-agent.service with the env vars inline
#   5. Enables and starts the service; tails journal briefly to prove it's alive

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "error: must run as root (try 'sudo ...')" >&2
  exit 1
fi

: "${PULSE_SERVER_URL:?PULSE_SERVER_URL is required}"
: "${PULSE_ENROLLMENT_TOKEN:?PULSE_ENROLLMENT_TOKEN is required}"
: "${PULSE_HOSTNAME:=$(hostname)}"
# PULSE_REPORTED_IP is optional — agent enumerates all interfaces by MAC and reports
# them to the server, where admin classifies test/management/ignored in the UI. If
# you leave it unset, the agent auto-detects a primary IP via the default-route
# connect trick (still OK as a placeholder until admin assigns a role).
: "${PULSE_REPORTED_IP:=}"
if [[ -z "$PULSE_REPORTED_IP" ]]; then
  echo "[pulse] hostname=$PULSE_HOSTNAME reported_ip=(auto-detect from interfaces)"
else
  echo "[pulse] hostname=$PULSE_HOSTNAME reported_ip=$PULSE_REPORTED_IP"
fi

SOURCE_TAR="${PULSE_SOURCE_TAR:-$(pwd)/pulse-source.tar.gz}"
if [[ ! -f "$SOURCE_TAR" ]]; then
  echo "error: source tarball not found at $SOURCE_TAR" >&2
  echo "       pass PULSE_SOURCE_TAR=/path/to/pulse-source.tar.gz or place it in cwd" >&2
  exit 1
fi

INSTALL_ROOT=/opt/pulse
SRC_DIR=$INSTALL_ROOT/src
VENV_DIR=$INSTALL_ROOT/.venv
DATA_DIR=/var/lib/pulse
TOKEN_FILE=$DATA_DIR/agent.token
UNIT_PATH=/etc/systemd/system/pulse-agent.service

echo "[pulse] installing system packages"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip iputils-ping iperf3 iw >/dev/null

echo "[pulse] extracting source to $SRC_DIR"
mkdir -p "$INSTALL_ROOT"
rm -rf "$SRC_DIR"
mkdir -p "$SRC_DIR"
tar xzf "$SOURCE_TAR" -C "$SRC_DIR" --strip-components=1

echo "[pulse] creating venv at $VENV_DIR (fresh)"
# Wipe any previous venv — re-installing on top of an old one can leave a half-baked
# state (missing bin/pip, partial ensurepip bundle, etc). A clean venv every run is
# cheap and reliable.
rm -rf "$VENV_DIR"
python3 -m venv "$VENV_DIR"
if [[ ! -x "$VENV_DIR/bin/pip" ]]; then
  echo "[pulse] venv built without pip — trying ensurepip bootstrap" >&2
  "$VENV_DIR/bin/python" -m ensurepip --upgrade
fi

# If the source tarball shipped with pre-built wheels (agent-wheels/), install fully
# offline — pip never touches pypi. Works on air-gapped hosts or anywhere outbound to
# pypi is blocked. Falls back to online install when the dir is absent (e.g. a lean
# dev tarball built without the server image).
WHEELS_DIR="$SRC_DIR/agent-wheels"
if [[ -d "$WHEELS_DIR" ]]; then
  echo "[pulse] offline install from $WHEELS_DIR"
  "$VENV_DIR/bin/pip" install --no-index --find-links="$WHEELS_DIR" --upgrade --quiet pip \
    || "$VENV_DIR/bin/pip" install --upgrade --quiet pip
  "$VENV_DIR/bin/pip" install --no-index --find-links="$WHEELS_DIR" --quiet -e "$SRC_DIR[agent]"
else
  echo "[pulse] online install (no agent-wheels/ in source)"
  "$VENV_DIR/bin/pip" install --upgrade --quiet pip
  "$VENV_DIR/bin/pip" install --quiet -e "$SRC_DIR[agent]"
fi

echo "[pulse] preparing data dir $DATA_DIR"
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"

# If a previous install left a token here and we're re-running with a different
# server URL or a fresh enrollment, clear it so the new enrollment actually runs.
if [[ "${PULSE_FORCE_REENROLL:-0}" == "1" ]]; then
  rm -f "$TOKEN_FILE" "$TOKEN_FILE.pending"
  echo "[pulse] cleared previous token (PULSE_FORCE_REENROLL=1)"
fi

echo "[pulse] writing systemd unit $UNIT_PATH"
cat >"$UNIT_PATH" <<UNIT
[Unit]
Description=Pulse agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$VENV_DIR/bin/pulse-agent
Restart=on-failure
RestartSec=5
Environment=PULSE_SERVER_URL=$PULSE_SERVER_URL
Environment=PULSE_ENROLLMENT_TOKEN=$PULSE_ENROLLMENT_TOKEN
Environment=PULSE_HOSTNAME=$PULSE_HOSTNAME
Environment=PULSE_REPORTED_IP=$PULSE_REPORTED_IP
Environment=PULSE_TOKEN_FILE=$TOKEN_FILE
Environment=PULSE_LOG_LEVEL=INFO

[Install]
WantedBy=multi-user.target
UNIT

chmod 0644 "$UNIT_PATH"

echo "[pulse] enabling + starting pulse-agent"
systemctl daemon-reload
systemctl enable pulse-agent.service >/dev/null
systemctl restart pulse-agent.service

sleep 2
echo
echo "[pulse] systemctl status (short):"
systemctl status pulse-agent.service --no-pager -n 0 || true
echo
echo "[pulse] journal tail (last 10 lines):"
journalctl -u pulse-agent.service -n 10 --no-pager || true
echo
echo "[pulse] install complete. Approve this agent on the server:"
echo
echo "  curl -s -H 'authorization: Bearer <admin-token>' ${PULSE_SERVER_URL%/}/v1/admin/enrollments/pending"
echo
