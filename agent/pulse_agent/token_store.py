"""Read and write the per-agent bearer token on local disk.

On first run the token file doesn't exist → the agent enrolls, fetches its token, and
writes it here (mode 0600). On subsequent runs the agent reads the token and skips
enrollment. The agent's stable identity (agent_uid) is persisted alongside the token so
the agent doesn't need to re-enroll if it crashes after the initial /enroll but before
approval.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredToken:
    agent_uid: str
    agent_token: str


def load(path: str | Path) -> StoredToken | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return StoredToken(agent_uid=data["agent_uid"], agent_token=data["agent_token"])
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def save(path: str | Path, token: StoredToken) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({"agent_uid": token.agent_uid, "agent_token": token.agent_token}))
    if os.name == "posix":
        os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def save_uid_only(path: str | Path, agent_uid: str) -> None:
    """Persist the agent_uid before approval so a crash doesn't cause re-enrollment."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".pending.tmp")
    tmp.write_text(json.dumps({"agent_uid": agent_uid, "agent_token": None}))
    if os.name == "posix":
        os.chmod(tmp, 0o600)
    os.replace(tmp, p.with_suffix(p.suffix + ".pending"))


def load_pending_uid(path: str | Path) -> str | None:
    p = Path(path).with_suffix(Path(path).suffix + ".pending")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("agent_uid")
    except (OSError, json.JSONDecodeError):
        return None


def clear_pending_uid(path: str | Path) -> None:
    p = Path(path).with_suffix(Path(path).suffix + ".pending")
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def clear(path: str | Path) -> None:
    p = Path(path)
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    clear_pending_uid(path)
