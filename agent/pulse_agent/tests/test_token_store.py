"""Token store round-trip."""

from __future__ import annotations

from pathlib import Path

from pulse_agent import token_store


def test_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "agent.token"
    assert token_store.load(p) is None

    token_store.save(p, token_store.StoredToken(agent_uid="u1", agent_token="t1"))
    loaded = token_store.load(p)
    assert loaded is not None
    assert loaded.agent_uid == "u1"
    assert loaded.agent_token == "t1"


def test_pending_uid_isolation(tmp_path: Path) -> None:
    p = tmp_path / "agent.token"
    token_store.save_uid_only(p, "uid-x")
    assert token_store.load_pending_uid(p) == "uid-x"
    # Main token file is still absent.
    assert token_store.load(p) is None
    token_store.clear_pending_uid(p)
    assert token_store.load_pending_uid(p) is None


def test_clear(tmp_path: Path) -> None:
    p = tmp_path / "agent.token"
    token_store.save(p, token_store.StoredToken(agent_uid="u", agent_token="t"))
    token_store.save_uid_only(p, "u")
    token_store.clear(p)
    assert token_store.load(p) is None
    assert token_store.load_pending_uid(p) is None
