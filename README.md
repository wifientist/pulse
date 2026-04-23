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

### Air-gapped / no-internet agents

The server's Docker image bakes pre-built Python wheels for every agent dependency into
the source tarball at `/app/agent-source.tar.gz`. Target hosts only need to reach the
**Pulse server** (never pypi). The installer auto-detects the bundled wheels and runs
`pip install --no-index --find-links=…`.

What the target host still needs:
- **Python 3.12** and **`python3-venv`** (normally pre-installed on Ubuntu server
  images; otherwise pre-bake them into your LXC template or serve from a local apt mirror).
- **`iputils-ping`** and **`iperf3`** (same options). Only apt packages the installer
  would otherwise fetch.

Everything else — httpx, icmplib, psutil, hatchling, the whole Python dep tree — ships
inside the tarball. Zero internet at install time on the target.

## Web UI

An admin SPA ships with the server. The Docker image builds it and serves it at `/`;
the API continues to live under `/v1/*`. Sign in at `/login` with your admin bearer
token.

### Dev workflow

Run the backend and the Vite dev server in two terminals:

```bash
# terminal 1 — backend on :8080
PULSE_DB_PATH=./pulse.sqlite .venv/bin/alembic upgrade head
PULSE_DB_PATH=./pulse.sqlite PULSE_ADMIN_TOKEN=dev-admin-token .venv/bin/pulse-server

# terminal 2 — web on :5173 (proxies /v1/* to :8080)
cd web && npm install && npm run dev
```

Visit `http://localhost:5173`, paste the admin token, you're in.

### Prod build

```bash
docker build -t pulse-server:latest -f docker/server.Dockerfile .
docker run -p 8080:8080 -e PULSE_ADMIN_TOKEN=... pulse-server:latest
# visit http://<host>:8080/
```

## Architecture

- **Server**: Python 3.12, FastAPI, SQLAlchemy 2.0 async, SQLite (WAL), APScheduler.
- **Agent**: Python 3.12, httpx, icmplib (with subprocess `ping` fallback on Windows / unprivileged containers).
- **Web**: React 18 + Vite + TypeScript + Tailwind; single long-lived SSE stream (`/v1/admin/events`) drives all dashboard state.
- **Transport**: HTTP polling agent→server; SSE push server→UI.
- **Auth**: pre-shared enrollment token → admin approval → per-agent bearer token. Admin API + UI is a single bearer token from env var.

See `/home/chris/.claude/plans/flickering-knitting-music.md` for the full design doc.

## Repo layout

```
pulse/
  pyproject.toml                 # workspace — [server] and [agent] optional deps
  docker/                        # Dockerfiles + compose
  shared/pulse_shared/           # Pydantic DTOs shared on the wire
  server/pulse_server/           # FastAPI app, DB, services, scheduler
  agent/pulse_agent/             # Agent main loop, pinger, probes
  web/                           # Vite + React + TS admin UI
  scripts/                       # dev + smoke-test scripts
```

## Status

Under construction. See the plan file for implementation order.
