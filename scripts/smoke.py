"""End-to-end smoke test for the multi-subnet Docker Compose stack.

Usage (requires Docker):

    docker compose -f docker/docker-compose.smoketest.yml up -d --build
    python3 scripts/smoke.py

Asserts:
  * All agents enroll, admin can approve them.
  * A full mesh of link_states transitions to `up`.
  * An iperf3 pair test between two agents succeeds with non-zero throughput.
  * A peer cut (docker network disconnect) eventually marks affected links `down`.
  * Recovery after reconnect returns the links to `up`.

When running against the compose stack, set `PULSE_SERVER_URL=http://127.0.0.1:18080` and
`PULSE_ADMIN_TOKEN=smoke-admin-token` in the environment first.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import httpx


def _wait_until(predicate, timeout_s: float, interval_s: float = 1.0, message: str = "") -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval_s)
    raise AssertionError(f"timeout after {timeout_s}s waiting: {message}")


def _mint_enrollment_token(http: httpx.Client, admin: dict[str, str], label: str) -> str:
    r = http.post("/v1/admin/enrollment-tokens", headers=admin, json={"label": label})
    r.raise_for_status()
    return r.json()["plaintext"]


def _approve_all_pending(http: httpx.Client, admin: dict[str, str]) -> list[str]:
    r = http.get("/v1/admin/enrollments/pending", headers=admin)
    r.raise_for_status()
    uids: list[str] = []
    for p in r.json():
        http.post(
            f"/v1/admin/enrollments/{p['id']}/approve", headers=admin, json={}
        ).raise_for_status()
        uids.append(p["agent_uid"])
    return uids


def _link_states_all_up(http: httpx.Client, admin: dict[str, str], expected_pairs: int) -> bool:
    r = http.get("/v1/admin/pings/latest", headers=admin)
    if r.status_code != 200:
        return False
    rows = r.json()
    up = [row for row in rows if row["state"] == "up"]
    return len(up) >= expected_pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="Pulse Docker smoke test")
    parser.add_argument("--server-url", default=os.environ.get("PULSE_SERVER_URL", "http://127.0.0.1:18080"))
    parser.add_argument("--admin-token", default=os.environ.get("PULSE_ADMIN_TOKEN", "smoke-admin-token"))
    parser.add_argument("--expected-agents", type=int, default=3)
    parser.add_argument("--enrollment-token-label", default="smoke")
    parser.add_argument("--skip-disconnect", action="store_true", help="Skip the network-disconnect phase")
    parser.add_argument("--iperf3-duration-s", type=int, default=3)
    parser.add_argument("--network-to-cut", default="pulse_subnet-bravo")
    parser.add_argument("--agent-to-cut", default="pulse-agent-bravo")
    args = parser.parse_args()

    admin = {"authorization": f"Bearer {args.admin_token}"}
    with httpx.Client(base_url=args.server_url, timeout=30.0) as http:
        print(f"[smoke] server: {args.server_url}")
        _wait_until(
            lambda: http.get("/healthz").status_code == 200,
            timeout_s=60,
            message="server healthz",
        )

        # Mint a pre-shared enrollment token and inject it into agents via env.
        # Operator-driven flow assumes the token has already been shared with agents at
        # bring-up time, so this script mints one, bounces agents, and approves. We rely
        # on the compose stack reading PULSE_ENROLLMENT_TOKEN from env.
        plain = _mint_enrollment_token(http, admin, args.enrollment_token_label)
        os.environ["PULSE_ENROLLMENT_TOKEN"] = plain
        print(f"[smoke] enrollment_token={plain[:12]}…")
        subprocess.check_call(
            ["docker", "compose", "-f", "docker/docker-compose.smoketest.yml", "restart"]
            + [f"pulse-agent-{name}" for name in ("alpha", "bravo", "charlie")][: args.expected_agents]
        )

        # Wait for all agents to appear as pending.
        _wait_until(
            lambda: len(http.get("/v1/admin/enrollments/pending", headers=admin).json())
            == args.expected_agents,
            timeout_s=60,
            message=f"{args.expected_agents} agents pending",
        )
        agent_uids = _approve_all_pending(http, admin)
        print(f"[smoke] approved agents: {agent_uids}")

        expected_pairs = args.expected_agents * (args.expected_agents - 1)
        _wait_until(
            lambda: _link_states_all_up(http, admin, expected_pairs),
            timeout_s=90,
            message=f"{expected_pairs} link_states=up",
        )
        print(f"[smoke] mesh is up ({expected_pairs} edges)")

        # iperf3 pair between agents 0 and 1.
        iperf_body = {
            "type": "iperf3_pair",
            "client_agent_uid": agent_uids[0],
            "server_agent_uid": agent_uids[1],
            "spec": {"duration_s": args.iperf3_duration_s, "protocol": "tcp"},
        }
        r = http.post("/v1/admin/tests", headers=admin, json=iperf_body)
        r.raise_for_status()
        test_id = r.json()["test_id"]
        print(f"[smoke] iperf3 test id={test_id}")

        def _finished():
            body = http.get(f"/v1/admin/tests/{test_id}", headers=admin).json()
            return body["state"] in ("succeeded", "failed", "timeout", "cancelled")

        _wait_until(_finished, timeout_s=60, message="iperf3 finished")
        body = http.get(f"/v1/admin/tests/{test_id}", headers=admin).json()
        assert body["state"] == "succeeded", body
        bps = body["result"]["throughput_bps"]
        assert bps and bps > 0, body
        print(f"[smoke] iperf3 throughput={bps:,} bps")

        if args.skip_disconnect:
            print("[smoke] done (skipping disconnect phase)")
            return 0

        # Cut bravo's subnet and wait for its links to go down.
        print(f"[smoke] disconnecting {args.agent_to_cut} from {args.network_to_cut}")
        subprocess.check_call(
            ["docker", "network", "disconnect", args.network_to_cut, args.agent_to_cut]
        )
        subprocess.check_call(
            ["docker", "network", "disconnect", "pulse_shared", args.agent_to_cut]
        )

        def _some_down():
            rows = http.get("/v1/admin/pings/latest", headers=admin).json()
            return any(r["state"] == "down" for r in rows)

        _wait_until(_some_down, timeout_s=120, message="at least one link_state=down")
        print("[smoke] disconnect detected")

        # Reconnect and verify recovery.
        subprocess.check_call(
            ["docker", "network", "connect", "pulse_shared", args.agent_to_cut]
        )
        subprocess.check_call(
            ["docker", "network", "connect", args.network_to_cut, args.agent_to_cut]
        )
        _wait_until(
            lambda: _link_states_all_up(http, admin, expected_pairs),
            timeout_s=120,
            message="mesh recovered",
        )
        print("[smoke] mesh recovered — done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
