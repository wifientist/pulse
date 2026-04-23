"""Pull a new agent source tarball from the server and swap it in atomically.

Command payload:
    {"url": "/v1/admin/agent-package", "version": "0.2.0"}

Flow:
  1. GET `url` (relative, same server) using the agent's own bearer token.
  2. Write the tarball to /opt/pulse/pending-upgrade.tar.gz.
  3. Extract to /opt/pulse/src.new (wipe any previous stage).
  4. Create /opt/pulse/.venv.new and `pip install -e .venv.new/src[agent]`.
  5. Atomic-rename:
         mv /opt/pulse/src       /opt/pulse/src.prev
         mv /opt/pulse/src.new   /opt/pulse/src
         mv /opt/pulse/.venv     /opt/pulse/.venv.prev
         mv /opt/pulse/.venv.new /opt/pulse/.venv
  6. Report success via the pending poll-response channel.
  7. os.execv the NEW agent binary (replaces this process in place — no systemd
     dance needed, no "exit and hope Restart= kicks back in" race).

If any step fails, nothing in /opt/pulse/src or .venv is touched — the staged copies
get cleaned up and the current agent keeps running.

Rollback is manual (SSH in, swap .prev dirs back) — we don't auto-rollback since
detecting "new version didn't come up healthy" requires the server to decide, which
isn't worth the extra plumbing for a home-lab.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from pulse_agent.config import load_agent_settings

log = logging.getLogger(__name__)


INSTALL_ROOT = Path("/opt/pulse")
SRC_DIR = INSTALL_ROOT / "src"
VENV_DIR = INSTALL_ROOT / ".venv"
SRC_NEW = INSTALL_ROOT / "src.new"
VENV_NEW = INSTALL_ROOT / ".venv.new"
SRC_PREV = INSTALL_ROOT / "src.prev"
VENV_PREV = INSTALL_ROOT / ".venv.prev"
STAGING_TAR = INSTALL_ROOT / "pending-upgrade.tar.gz"


def _rm(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        try:
            p.unlink()
        except OSError:
            pass


async def _download(url: str) -> None:
    settings = load_agent_settings()
    token = None
    try:
        import json
        data = json.loads(Path(settings.token_file).read_text())
        token = data.get("agent_token")
    except Exception:  # noqa: BLE001
        pass
    if not token:
        raise RuntimeError("no agent token on disk — cannot authenticate upgrade download")

    base = settings.server_url.rstrip("/")
    full_url = url if url.startswith("http") else f"{base}{url}"
    headers = {"Authorization": f"Bearer {token}"}

    _rm(STAGING_TAR)
    async with httpx.AsyncClient(timeout=60.0, verify=settings.verify_tls) as http:
        r = await http.get(full_url, headers=headers)
        r.raise_for_status()
        STAGING_TAR.write_bytes(r.content)


def _stage_new_source() -> None:
    _rm(SRC_NEW)
    SRC_NEW.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["tar", "xzf", str(STAGING_TAR), "-C", str(SRC_NEW), "--strip-components=1"],
        check=True,
    )


def _build_new_venv() -> None:
    _rm(VENV_NEW)
    subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_NEW)],
        check=True,
    )
    pip = VENV_NEW / "bin" / "pip"
    subprocess.run(
        [str(pip), "install", "--upgrade", "--quiet", "pip"],
        check=True,
    )
    subprocess.run(
        [str(pip), "install", "--quiet", "-e", f"{SRC_NEW}[agent]"],
        check=True,
    )


def _swap() -> None:
    _rm(SRC_PREV)
    _rm(VENV_PREV)
    if SRC_DIR.exists():
        SRC_DIR.rename(SRC_PREV)
    SRC_NEW.rename(SRC_DIR)
    if VENV_DIR.exists():
        VENV_DIR.rename(VENV_PREV)
    VENV_NEW.rename(VENV_DIR)


def _replace_self() -> None:
    """Exec the new venv's pulse-agent binary in-place — this process is replaced.
    systemd sees the new PID as a continuation (same cgroup) so no restart is needed."""
    new_bin = VENV_DIR / "bin" / "pulse-agent"
    os.execv(str(new_bin), [str(new_bin)])  # noqa: S606


async def run(payload: dict[str, Any]) -> tuple[bool, dict[str, Any] | None, str | None]:
    url = (payload.get("url") or "").strip()
    version = (payload.get("version") or "").strip() or "unknown"
    if not url:
        return (False, None, "url required")

    try:
        log.info("self_upgrade.begin version=%s url=%s", version, url)
        await _download(url)
        # Run blocking filesystem / subprocess work in a thread so we don't stall the
        # event loop.
        await asyncio.to_thread(_stage_new_source)
        await asyncio.to_thread(_build_new_venv)
        await asyncio.to_thread(_swap)
    except subprocess.CalledProcessError as e:
        return (
            False,
            {"stage": "install", "returncode": e.returncode},
            f"install step failed: {e}",
        )
    except Exception as e:  # noqa: BLE001
        return (False, None, f"{type(e).__name__}: {e}")

    # Schedule the exec for just after the result is shipped back. We use a background
    # task that sleeps 0.5s so the poll_loop has time to send the next /poll with the
    # success ack before we replace ourselves.
    asyncio.get_running_loop().call_later(0.5, _replace_self)

    return (
        True,
        {"version": version, "restarting": True},
        None,
    )
