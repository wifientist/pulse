"""Local-process smoke test — no Docker required.

Spawns the server + N agents as separate processes on one machine, drives the enrollment
flow, verifies the mesh comes up, runs an iperf3 pair test (if iperf3 is on PATH), and
tears everything down.

Every agent reports 127.0.0.1 as its primary_ip — so this exercises the plumbing but not
real multi-subnet routing. Use scripts/smoke.py with Docker for the full picture.

Usage:

    python3 scripts/smoke_local.py --agents 3

The script uses the project's existing venv (./.venv/bin/python) to exec the server and
agent entrypoints.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"
VENV_ALEMBIC = REPO_ROOT / ".venv" / "bin" / "alembic"
SERVER_BIN = REPO_ROOT / ".venv" / "bin" / "pulse-server"
AGENT_BIN = REPO_ROOT / ".venv" / "bin" / "pulse-agent"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until(predicate, timeout_s: float, interval_s: float = 0.5, message: str = "") -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(interval_s)
    raise AssertionError(f"timeout after {timeout_s}s: {message}")


def _run_alembic(db_path: Path) -> None:
    env = {**os.environ, "PULSE_DB_PATH": str(db_path)}
    subprocess.check_call(
        [str(VENV_ALEMBIC), "upgrade", "head"], cwd=REPO_ROOT, env=env
    )


def _start_server(port: int, db_path: Path, admin_token: str, log_path: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "PULSE_DB_PATH": str(db_path),
        "PULSE_ADMIN_TOKEN": admin_token,
        "PULSE_BIND_HOST": "127.0.0.1",
        "PULSE_BIND_PORT": str(port),
        "PULSE_DEFAULT_POLL_INTERVAL_S": "1",
        "PULSE_DEFAULT_PING_INTERVAL_S": "1",
        "PULSE_MIN_DWELL_S": "2",
        "PULSE_RECOVERY_WINDOW_S": "2",
    }
    log = open(log_path, "wb")
    return subprocess.Popen(
        [str(SERVER_BIN)],
        cwd=REPO_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def _start_agent(
    name: str,
    server_url: str,
    enrollment_token: str,
    token_file: Path,
    log_path: Path,
) -> subprocess.Popen:
    env = {
        **os.environ,
        "PULSE_SERVER_URL": server_url,
        "PULSE_ENROLLMENT_TOKEN": enrollment_token,
        "PULSE_TOKEN_FILE": str(token_file),
        "PULSE_HOSTNAME": name,
        "PULSE_REPORTED_IP": "127.0.0.1",
        "PULSE_LOG_LEVEL": "INFO",
    }
    log = open(log_path, "wb")
    return subprocess.Popen(
        [str(AGENT_BIN)],
        cwd=REPO_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def _admin_headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Local-process smoke test for Pulse")
    parser.add_argument("--agents", type=int, default=3)
    parser.add_argument("--keep-tmp", action="store_true", help="Preserve the temp dir for postmortem inspection")
    parser.add_argument("--iperf3", action="store_true", help="Run an iperf3 pair test (requires iperf3 on PATH)")
    args = parser.parse_args()

    if not VENV_PY.exists():
        print(f"[smoke-local] missing venv at {VENV_PY}; create one and install the project first", file=sys.stderr)
        return 1

    admin_token = "smoke-local-admin"
    port = _free_port()
    server_url = f"http://127.0.0.1:{port}"
    tmp = Path(tempfile.mkdtemp(prefix="pulse-smoke-"))
    print(f"[smoke-local] tmp={tmp}")
    procs: list[subprocess.Popen] = []
    failed = False

    try:
        db_path = tmp / "pulse.sqlite"
        _run_alembic(db_path)
        server_log = tmp / "server.log"
        server = _start_server(port, db_path, admin_token, server_log)
        procs.append(server)

        with httpx.Client(base_url=server_url, timeout=10.0) as http:
            _wait_until(
                lambda: http.get("/healthz").status_code == 200,
                timeout_s=10,
                message="server healthz",
            )
            print("[smoke-local] server up")

            headers = _admin_headers(admin_token)
            r = http.post(
                "/v1/admin/enrollment-tokens", headers=headers, json={"label": "smoke"}
            )
            r.raise_for_status()
            plain = r.json()["plaintext"]

            for i in range(args.agents):
                name = f"agent-{i}"
                token_file = tmp / f"{name}.token"
                log = tmp / f"{name}.log"
                procs.append(
                    _start_agent(name, server_url, plain, token_file, log)
                )
                print(f"[smoke-local] started {name}")

            _wait_until(
                lambda: len(http.get("/v1/admin/enrollments/pending", headers=headers).json())
                == args.agents,
                timeout_s=30,
                message=f"{args.agents} agents pending",
            )
            pending = http.get("/v1/admin/enrollments/pending", headers=headers).json()
            agent_uids = []
            for p in pending:
                http.post(
                    f"/v1/admin/enrollments/{p['id']}/approve",
                    headers=headers,
                    json={},
                ).raise_for_status()
                agent_uids.append(p["agent_uid"])
            print(f"[smoke-local] approved {len(agent_uids)} agents")

            expected_pairs = args.agents * (args.agents - 1)

            # Give agents time to accumulate a few samples per pair.
            time.sleep(5)
            # Force the server to roll the current samples into a minute bucket and run
            # alert evaluation. The debug endpoint simulates a future wall-clock.
            r = http.post("/v1/admin/debug/rollup-now", headers=headers)
            r.raise_for_status()
            print(f"[smoke-local] forced rollup: {r.json()}")

            def _all_up() -> bool:
                rows = http.get("/v1/admin/pings/latest", headers=headers).json()
                ups = [r for r in rows if r["state"] == "up"]
                return len(ups) >= expected_pairs

            # A single rollup may not promote past dwell — fire a couple in sequence.
            for _ in range(4):
                if _all_up():
                    break
                time.sleep(3)
                http.post("/v1/admin/debug/rollup-now", headers=headers).raise_for_status()

            _wait_until(_all_up, timeout_s=30, message=f"{expected_pairs} link_states up")
            print(f"[smoke-local] mesh up ({expected_pairs} edges)")

            if args.iperf3:
                if shutil.which("iperf3") is None:
                    print("[smoke-local] WARN: iperf3 not on PATH, skipping pair test")
                else:
                    body = {
                        "type": "iperf3_pair",
                        "client_agent_uid": agent_uids[0],
                        "server_agent_uid": agent_uids[1],
                        "spec": {"duration_s": 2, "protocol": "tcp"},
                    }
                    r = http.post("/v1/admin/tests", headers=headers, json=body)
                    r.raise_for_status()
                    test_id = r.json()["test_id"]

                    def _done() -> bool:
                        st = http.get(f"/v1/admin/tests/{test_id}", headers=headers).json()["state"]
                        return st in ("succeeded", "failed", "timeout", "cancelled")

                    _wait_until(_done, timeout_s=30, message="iperf3 finished")
                    state = http.get(f"/v1/admin/tests/{test_id}", headers=headers).json()
                    if state["state"] != "succeeded":
                        print(f"[smoke-local] iperf3 state={state['state']} error={state.get('error')}")
                    else:
                        print(f"[smoke-local] iperf3 throughput={state['result']['throughput_bps']:,} bps")
    except Exception as e:  # noqa: BLE001
        failed = True
        print(f"[smoke-local] FAILED: {e}", file=sys.stderr)
    finally:
        for p in procs:
            with contextlib.suppress(Exception):
                p.send_signal(signal.SIGTERM)
        for p in procs:
            with contextlib.suppress(Exception):
                p.wait(timeout=5)
        for p in procs:
            if p.returncode is None:
                with contextlib.suppress(Exception):
                    p.kill()
        if not args.keep_tmp and not failed:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"[smoke-local] artifacts left at {tmp}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
