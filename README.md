# Pulse

**Peer Uptime & Link Status Engine** — a self-hosted mesh connectivity monitor for home labs.

A central FastAPI server ("brain") plus lightweight Python agents installed on devices
across your LAN subnets. Agents run continuous periodic pings in a full mesh, plus to
admin-defined passive targets (routers, APs, printers…), and report link state
(RTT / jitter / loss) to the server. On demand you can trigger TCP probes, DNS lookups,
HTTP checks, or coordinated iperf3 pair tests. Webhook alerts fire on link state
changes.

## Features

**Mesh monitoring**
- Continuous pair-wise ICMP between every pair of active agents (every ~5s by default).
- Per-agent *boost* toggle flips an agent's outbound cadence to 1 Hz for a time-bounded
  window (5m / 20m / 60m; auto-expires) so you can get fine-grained data during a
  diagnostic session without forgetting to turn it off.
- **Test-plane isolation**: the server hands each agent its own role=test interface
  IP as a socket bind source, so pings physically leave via the test plane regardless
  of the host's default route. Management-plane traffic stays on the management plane.

**Passive targets** — devices that can't run an agent (router, AP mgmt IP, printer).
All active agents ping each enabled target; the UI surfaces a worst-of-agents state
badge so a target going down on one agent but not others is instantly visible.

**Interface inventory & remote control**
- Agents report every non-virtual interface on every poll — MAC, current IP, iface
  name, and wireless details (SSID / BSSID / signal dBm) when applicable.
- DHCP release/renew any interface remotely.
- Self-upgrade: push the latest agent version from the UI. The agent downloads the
  baked source tarball, rebuilds its venv from bundled wheels (no pypi), rewrites
  shebangs + `.pth` paths so the post-swap venv resolves correctly, and
  `execve`s itself in place. No systemd dance.

**Wireless visibility**
- SSID / BSSID / signal captured via `iw dev <iface> link` on every poll
  (no sudo required). Wireless samples are sanitized server-side — readings
  outside the physically realistic −10 to −100 dBm window are dropped before
  storage so a single driver glitch can't distort the chart axes.
- Admin-curated **Access Points** section maps BSSIDs to AP names. Each AP can own
  N BSSIDs — Ruckus-style multi-SSID radios have their full BSSID set attached to
  one entry. Observed-but-unmapped BSSIDs surface in an "Unassigned" list for
  one-click assignment, populated from both client connections AND monitor-agent
  airspace scans (so a BSSID nobody connected to still becomes assignable).
- Trends draws a per-client color-segmented signal chart with vertical roam
  markers + consolidated Roam events list, plus a "All wireless clients"
  overlay chart whose hover tooltip surfaces AP name and band badge per
  client (2.4 / 5 / 6 GHz).

**Airspace monitoring** — a dedicated *monitor-role* agent that doesn't
participate in the ping mesh and instead runs `iw dev <iface> scan` on every
poll, reporting every visible BSSID's signal + frequency. Server filters
incoming scans through an admin-curated **Monitored SSIDs** allowlist before
storing, so the table stays focused on the networks you care about. Trends
page gets an Airspace panel per monitor agent with per-band toggle chips
(2.4 / 5 / 6 GHz) and AP-aware coloring/legend. Monitor agents are
automatically excluded from the mesh — no pings in or out, no orphan nodes
on the dashboard.

**Tools** — a framework for one-shot, server-orchestrated infra tests
that capture pre-run state, drive a sequence of mutations, and restore on
finish. First tool: **Attenuator**, which integrates with the Ruckus One
cloud API to ramp AP transmit power on a planned schedule (or jump
straight to a target with `instant` mode). Use cases: deterministic roam
tests, drowning out a target AP to validate failover, simulating a noisy
neighbor. Reusable presets, one-at-a-time runs with crash recovery on
startup, and per-step audit log including each Ruckus activity ID.

**Trends & deep dives**
- Historical per-pair time-series (loss / jitter / latency percentiles) with
  automatic granularity: raw tier for ≤2h, minute aggregates 2h-24h, hour
  aggregates beyond. Range presets from 1m through 7d.
- Short ranges auto-refresh so a live boost flows in without clicking.
- "Boost both" button on the Trends page turns on 1 Hz for the selected pair
  from one place.

**Live dashboard**
- SSE stream (`/v1/admin/events`) pushes a full snapshot every few seconds;
  the whole UI reads from that one source.
- Mesh diagram (xyflow + dagre) with drag-to-rearrange nodes, persistent
  per-edge handle choices, one-way edges to passive targets, and an
  "auto-edge" button that re-picks geometrically-sensible edge endpoints
  without disturbing your node layout. Bidirectional agent pairs render as
  a single double-arrow line; hovering surfaces per-direction state + RTT
  + loss on separate lines.
- Global filter in the header narrows every page to a single agent (1:n
  focus) or a hand-picked subset.

**Alerts & webhooks**
- Dwell-based state machine (up / degraded / down / unknown) evaluated once
  per minute against per-pair aggregates. Default: 60s dwell to transition,
  120s recovery window. Thresholds (`degraded_loss_pct`, `down_loss_pct`,
  `degraded_rtt_p95_ms`) are env-configurable.
- Webhook fan-out on link-state transitions for agent↔agent links.

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

On first run the agent POSTs to `/v1/enroll` and shows up in the server's pending
list. Approve it in the UI (`/agents`) — the agent receives a per-device bearer
token and starts reporting within seconds.

### Air-gapped / no-internet agents

The server's Docker image bakes pre-built Python wheels for every agent dependency
into the source tarball at `/app/agent-source.tar.gz`. Target hosts only need to
reach the **Pulse server** (never pypi). `scripts/install-agent.sh` auto-detects the
bundled wheels and runs `pip install --no-index --find-links=…`.

What the target host still needs:
- **Python 3.12** and **`python3-venv`** (normally pre-installed on Ubuntu server
  images; otherwise pre-bake them into your LXC template or serve from a local apt mirror).
- **`iputils-ping`**, **`iperf3`**, **`iw`** (same options — `iw` only if the host
  has a wireless interface you want monitored).

Everything else — httpx, icmplib, psutil, hatchling, the whole Python dep tree —
ships inside the tarball. Zero internet at install time on the target.

## Web UI

An admin SPA ships with the server. The Docker image builds it and serves it at `/`;
the API continues to live under `/v1/*`. Sign in at `/login` with your admin bearer
token.

Pages:
- `/` — **Dashboard** with status tiles, live mesh diagram (filter / legend /
  auto-arrange / auto-edge / lock), recent alerts.
- `/agents` — agent list (expand any row for per-interface inventory + role
  assignment + DHCP renew + upgrade + boost), **Passive Targets** section,
  enrollment-token mint/revoke.
- `/trends` — pair-focused historical charts (RTT avg/p50/p95/p99, loss %,
  jitter) + wireless signal chart when applicable + Airspace panel(s) for
  monitor-role agents (with per-band toggle chips).
- `/access-points` — BSSID → AP-name mapping with an Unassigned list of
  observed-but-unmapped BSSIDs and a **Monitored SSIDs** allowlist for the
  airspace scanner.
- `/tools` — landing page for server-orchestrated infra tests.
- `/tools/attenuator` — Ruckus One AP txPower control with reusable
  presets, instant or ramped runs, and a live run banner.

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

- **Server**: Python 3.12, FastAPI, SQLAlchemy 2.0 async, SQLite (WAL),
  APScheduler. Optional Ruckus One cloud API client for the Attenuator
  tool — credentials configured per-deployment via env vars.
- **Agent**: Python 3.12, httpx, icmplib (with subprocess `ping` fallback on Windows
  / unprivileged containers). Optional `iw` for wireless detail and (on
  monitor-role agents) airspace scanning.
- **Web**: React 19 + Vite 6 + TypeScript 5 + Tailwind v4 + xyflow + recharts +
  zustand. Single long-lived SSE stream (`/v1/admin/events`) drives all dashboard state.
- **Transport**: HTTP polling agent→server; SSE push server→UI.
- **Auth**: pre-shared enrollment token → admin approval → per-agent bearer token.
  Admin API + UI is a single bearer token from env var.

### Data retention

- Raw ping samples: 48h (`raw_retention_hours`).
- Minute aggregates: 14 days (`minute_retention_days`).
- Hour aggregates: kept indefinitely (hundreds of kB per year at home-lab scale).
- Wireless samples + airspace scan samples + passive raw: same horizon as
  agent raw samples.
- Alerts / deep-dive-style sessions / enrollment token plaintext: until manually cleared
  (plaintext is one-shot — see auth above).

## Repo layout

```
pulse/
  pyproject.toml                 # workspace — [server] and [agent] optional deps
  docker/                        # Dockerfiles + compose
  shared/pulse_shared/           # Pydantic DTOs shared on the wire
  server/pulse_server/           # FastAPI app, DB, services, scheduler
  agent/pulse_agent/             # Agent main loop, pinger, probes
  web/                           # Vite + React + TS admin UI
  scripts/                       # dev + smoke-test scripts (install-agent.sh)
```

## Status

Active development. Recent focus: airspace monitoring (monitor-role
agents + iw scan + monitored-SSID allowlist), Tools framework with the
Attenuator (Ruckus One AP control with ramp/instant modes), band-aware
wireless charts, and dashboard polish (bidi edge consolidation,
per-direction tooltip).
