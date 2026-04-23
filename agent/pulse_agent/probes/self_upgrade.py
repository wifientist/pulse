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


# Process-local guard so even if the server (or a buggy older server) re-delivers the
# same in-flight upgrade command, only one actually runs. asyncio.Lock is fine here —
# the event loop is single-threaded and we only run one agent per process.
_UPGRADE_LOCK = asyncio.Lock()


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
    # Use `python -m pip` not the pip entry-stub. The stub imports pip's own modules
    # and a self-upgrade overwrites those mid-run, producing
    # `ModuleNotFoundError: pip._internal.exceptions`. Calling via the interpreter
    # works because python bootstraps a fresh importer on each process.
    new_py = VENV_NEW / "bin" / "python"
    wheels = SRC_NEW / "agent-wheels"
    if wheels.is_dir():
        # Offline / air-gapped path — install only from bundled wheels, matching
        # install-agent.sh. The wheels set is pinned to the server's image so the
        # agent never needs to reach pypi.
        subprocess.run(
            [str(new_py), "-m", "pip", "install", "--no-index",
             "--find-links", str(wheels), "--upgrade", "--quiet", "pip"],
            check=True,
        )
        subprocess.run(
            [str(new_py), "-m", "pip", "install", "--no-index",
             "--find-links", str(wheels), "--quiet", "-e", f"{SRC_NEW}[agent]"],
            check=True,
        )
    else:
        # Fallback: online install. Skip the pip self-upgrade — the venv's
        # bootstrap pip is good enough and avoids the self-overwrite risk.
        subprocess.run(
            [str(new_py), "-m", "pip", "install", "--quiet",
             "-e", f"{SRC_NEW}[agent]"],
            check=True,
        )

    # Belt-and-braces: the entry-point script we're about to exec must exist. If it
    # doesn't, something went wrong that didn't surface as a non-zero subprocess exit
    # (corrupted wheel, editables hook skipping, etc). Fail explicitly here so _swap
    # never runs against a half-baked venv.
    new_bin = VENV_NEW / "bin" / "pulse-agent"
    if not new_bin.exists():
        raise RuntimeError(
            f"expected agent entry-point not found at {new_bin} after install"
        )

    # Rewrite absolute paths that pip baked in at install time so they resolve after
    # the post-swap rename. Two sources of breakage:
    #   - Console-script shebangs in .venv.new/bin/* point at .venv.new/bin/python.
    #   - The editable .pth file in site-packages points at src.new.
    # After _swap these paths no longer exist and execve returns ENOENT on the shebang.
    _rewrite_baked_paths(
        VENV_NEW,
        replacements=[
            (str(VENV_NEW) + "/", str(VENV_DIR) + "/"),
            (str(SRC_NEW) + "/", str(SRC_DIR) + "/"),
        ],
    )


def _rewrite_baked_paths(
    venv: Path, replacements: list[tuple[str, str]],
) -> None:
    """Walk venv/bin and venv/lib/**/site-packages/*.pth and text-replace absolute
    paths. Binary (ELF) files are skipped. No-op if a candidate file doesn't start
    with `#!` or isn't plain UTF-8 text."""
    import glob
    targets: list[Path] = []
    bin_dir = venv / "bin"
    if bin_dir.is_dir():
        targets.extend(p for p in bin_dir.iterdir() if p.is_file())
    # Any .pth file under lib/*/site-packages — glob handles the pythonX.Y variance.
    targets.extend(Path(p) for p in glob.glob(str(venv / "lib" / "*" / "site-packages" / "*.pth")))

    for t in targets:
        try:
            data = t.read_bytes()
        except OSError:
            continue
        # Skip ELF and other binaries — shebang rewrite only applies to text scripts.
        if data[:4] == b"\x7fELF":
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            t.write_text(new_text)


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

    # No-op if we're already on the requested version — protects against duplicate /
    # replayed upgrade commands which would otherwise rebuild the venv against ourselves
    # and can leave the live .venv half-installed if anything fails.
    from pulse_shared.version import AGENT_VERSION
    if version != "unknown" and version == AGENT_VERSION:
        log.info("self_upgrade.skip already_at=%s", AGENT_VERSION)
        return (True, {"version": version, "restarting": False, "noop": True}, None)

    # Refuse a concurrent run. Without this, two self_upgrade tasks can race inside
    # /opt/pulse/src.new — one rm-rf's the tree the other is installing from, and
    # hatchling raises FileNotFoundError on getcwd().
    if _UPGRADE_LOCK.locked():
        log.warning("self_upgrade.skip already_in_progress")
        return (False, None, "upgrade already in progress")

    async with _UPGRADE_LOCK:
        return await _run_locked(url, version)


async def _run_locked(
    url: str, version: str,
) -> tuple[bool, dict[str, Any] | None, str | None]:
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
