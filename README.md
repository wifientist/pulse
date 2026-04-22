# Pulse

**Peer Uptime & Link Status Engine** — a self-hosted mesh connectivity monitor for home labs.

A central FastAPI server ("brain") plus lightweight Python agents installed on devices across your LAN subnets. Agents run continuous periodic pings in a full mesh and report link state (RTT / jitter / loss) to the server. On demand you can trigger TCP probes, DNS lookups, HTTP checks, or coordinated iperf3 pair tests. Webhook alerts fire on link state changes.

## Quick start (server)

```bash
cp .env.example .env
docker compose -f docker/docker-compose.yml up -d
```

## Quick start (agent)

```bash
pip install '.[agent]'
PULSE_SERVER_URL=https://pulse.example \
PULSE_ENROLLMENT_TOKEN=<token-from-admin> \
pulse-agent
```

On first run the agent POSTs to `/v1/enroll` and shows up in the server's pending list. Approve it in the admin API (or future UI); the agent then receives a per-device bearer token and starts reporting.

## Architecture

- **Server**: Python 3.12, FastAPI, SQLAlchemy 2.0 async, SQLite (WAL), APScheduler.
- **Agent**: Python 3.12, httpx, icmplib (with subprocess `ping` fallback on Windows / unprivileged containers).
- **Transport**: HTTP polling — agent POSTs telemetry and receives pending commands in one round-trip.
- **Auth**: pre-shared enrollment token → admin approval → per-agent bearer token. Admin API is a single bearer token from env var.

See `/home/chris/.claude/plans/flickering-knitting-music.md` for the full design doc.

## Repo layout

```
pulse/
  pyproject.toml                 # workspace — [server] and [agent] optional deps
  docker/                        # Dockerfiles + compose
  shared/pulse_shared/           # Pydantic DTOs shared on the wire
  server/pulse_server/           # FastAPI app, DB, services, scheduler
  agent/pulse_agent/             # Agent main loop, pinger, probes
  scripts/                       # dev + smoke-test scripts
```

## Status

Under construction. See the plan file for implementation order.
