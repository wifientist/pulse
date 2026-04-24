"""Attenuator tool — drives Ruckus AP transmit power in a planned ramp.

Participants are `AccessPoint` rows with a Ruckus serial. Each participant
has a `direction` ("drop" | "raise") and a `target_value` (MAX / -1..-10 / MIN).
On start the service snapshots each participant's current txPower so we can
restore. A background asyncio task then steps each participant's value
toward its target at `step_size_db` every `step_interval_s`. Any failed
Ruckus call halts the run and triggers restore.

Only one run may be active at a time; a second start() returns the existing
run unchanged. On server startup, `recover_stale_runs()` finds any run
stuck in `running` (from a crash) and restores it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pulse_server.config import Settings
from pulse_server.db.models import (
    AccessPoint,
    AttenuatorPreset,
    ToolRun,
    ToolRunStep,
)
from pulse_server.repo import meta_repo
from pulse_server.services import boost_service
from pulse_server.services.ruckus_client import (
    RADIO_KEYS,
    SUCCESS_STATUSES,
    TX_POWER_VALUES,
    RuckusClient,
    build_client,
)

log = logging.getLogger(__name__)

TOOL_TYPE = "attenuator"
MAX_RUN_SECONDS = 60 * 60  # 1h hard cap
STEP_ACTIVITY_TIMEOUT_S = 30.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _value_index(v: str) -> int:
    """Index into TX_POWER_VALUES — 'MAX' is 0, '-10' is 11, 'MIN' is 12."""
    return TX_POWER_VALUES.index(v)


def _ramp_sequence(start: str, target: str, step_size_db: int) -> list[str]:
    """Return the ordered list of txPower values from start (exclusive) to
    target (inclusive), stepping by `step_size_db` indices each tick. If the
    start is already past the target (shouldn't happen in normal setup) we
    return just [target] so the run converges in one step."""
    si = _value_index(start)
    ti = _value_index(target)
    out: list[str] = []
    if si == ti:
        return out
    direction = 1 if ti > si else -1
    cur = si
    while True:
        nxt = cur + direction * step_size_db
        if (direction > 0 and nxt >= ti) or (direction < 0 and nxt <= ti):
            out.append(TX_POWER_VALUES[ti])
            return out
        out.append(TX_POWER_VALUES[nxt])
        cur = nxt


# ----- state: one run max at a time -----------------------------------
_active_task: asyncio.Task | None = None
_active_run_id: int | None = None


def active_run_id() -> int | None:
    return _active_run_id


async def get_active_run(db: AsyncSession) -> ToolRun | None:
    row = (
        await db.execute(
            select(ToolRun).where(
                ToolRun.tool_type == TOOL_TYPE, ToolRun.state == "running",
            )
        )
    ).scalar_one_or_none()
    return row


# ----- start / cancel -------------------------------------------------


async def start_run(
    db: AsyncSession,
    settings: Settings,
    sessionmaker: async_sessionmaker,
    *,
    preset_id: int | None,
    name: str,
    radio: str,
    step_size_db: int,
    step_interval_s: int,
    participants: list[dict],
    boost_participants: bool,
    instant: bool = False,
) -> ToolRun:
    """Validate, snapshot current powers, persist the run, spawn the driver
    task. Does NOT wait for completion — returns the run row immediately.

    `instant=True` bypasses the ramp: each participant jumps directly to its
    target txPower in one Ruckus call, and the run does NOT restore on
    success (semantic: apply permanently). Restore still runs on cancel."""
    global _active_task, _active_run_id

    if radio not in RADIO_KEYS:
        raise ValueError(f"unknown radio: {radio}")
    # step_size + step_interval are only meaningful for the ramped path;
    # validate them there.
    if not instant:
        if step_size_db < 1 or step_size_db > 23:
            raise ValueError("step_size_db must be 1..23")
        if step_interval_s < 1 or step_interval_s > 120:
            raise ValueError("step_interval_s must be 1..120")
    if not participants:
        raise ValueError("at least one participant required")
    if instant:
        # One step per participant plus activity-confirmation headroom.
        duration_s = 120
    else:
        # Bounded run time: steps × interval, capped at MAX_RUN_SECONDS.
        # Worst case ~25 discrete txPower values (MAX + -1..-23 + MIN) ÷
        # step_size_db plus slack for activity confirmation.
        max_steps = 25 // step_size_db + 2
        duration_s = min(
            MAX_RUN_SECONDS, max(60, max_steps * step_interval_s + 30),
        )

    existing = await get_active_run(db)
    if existing is not None:
        raise RuntimeError(
            f"attenuator run already active (id={existing.id}); cancel it first"
        )

    # Resolve participants → access_point rows with Ruckus serials.
    ap_ids = {int(p["ap_id"]) for p in participants}
    ap_rows = (
        await db.execute(
            select(AccessPoint).where(AccessPoint.id.in_(ap_ids))
        )
    ).scalars().all()
    by_id = {r.id: r for r in ap_rows}
    missing = ap_ids - set(by_id)
    if missing:
        raise ValueError(f"unknown access_point id(s): {sorted(missing)}")
    for p in participants:
        ap = by_id[int(p["ap_id"])]
        if not ap.ruckus_serial:
            raise ValueError(
                f"AP '{ap.name}' has no Ruckus serial mapped yet"
            )

    # Snapshot current powers via Ruckus for every participant.
    client = build_client(settings)
    try:
        revert: list[dict[str, Any]] = []
        config_participants: list[dict[str, Any]] = []
        for p in participants:
            ap = by_id[int(p["ap_id"])]
            current = await client.get_ap_radio(ap.ruckus_serial)
            sub = current.get(RADIO_KEYS[radio]) or {}
            tx_now = sub.get("txPower") or "Auto"
            revert.append(
                {
                    "ap_id": ap.id,
                    "ap_serial": ap.ruckus_serial,
                    "radio": radio,
                    "tx_power": tx_now,
                }
            )
            config_participants.append(
                {
                    "ap_id": ap.id,
                    "ap_serial": ap.ruckus_serial,
                    "ap_name": ap.name,
                    "direction": p["direction"],
                    "target_value": p["target_value"],
                    "start_value": tx_now,
                }
            )
    finally:
        await client.aclose()

    now = _now_ms()
    run = ToolRun(
        tool_type=TOOL_TYPE,
        preset_id=preset_id,
        state="running",
        config={
            "name": name,
            "radio": radio,
            "step_size_db": step_size_db,
            "step_interval_s": step_interval_s,
            "boost_participants": boost_participants,
            "instant": instant,
            "participants": config_participants,
        },
        revert_state={"participants": revert},
        started_at=now,
        ends_at=now + duration_s * 1000,
        finalized_at=None,
        error=None,
    )
    db.add(run)
    await db.flush()
    run_id = run.id

    # Optional: boost every agent currently associated to any participating
    # BSSID so the Trends page has 1Hz data while the ramp runs.
    if boost_participants:
        await _boost_associated_agents(db, radio, config_participants)

    await db.commit()

    # Spawn driver task; store globals so the cancel path can cancel it.
    _active_run_id = run_id
    _active_task = asyncio.create_task(
        _drive_run(sessionmaker, settings, run_id)
    )
    return run


async def cancel_run(
    sessionmaker: async_sessionmaker, settings: Settings, run_id: int,
) -> None:
    global _active_task, _active_run_id
    if _active_task is not None and not _active_task.done():
        _active_task.cancel()
    # Regardless of the task state, try a clean restore.
    async with sessionmaker() as db:
        run = await db.get(ToolRun, run_id)
        if run is None:
            return
        if run.state != "running":
            return
        run.state = "cancelled"
        await _restore_and_finalize(db, settings, run)
        await db.commit()
    _active_task = None
    _active_run_id = None


# ----- recovery -------------------------------------------------------


async def recover_stale_runs(
    sessionmaker: async_sessionmaker, settings: Settings,
) -> int:
    """On server startup: anything still marked `running` is from a crash.
    Mark failed and try to restore. Returns the count handled."""
    async with sessionmaker() as db:
        rows = (
            await db.execute(
                select(ToolRun).where(
                    ToolRun.tool_type == TOOL_TYPE,
                    ToolRun.state == "running",
                )
            )
        ).scalars().all()
        for r in rows:
            r.state = "failed"
            r.error = (r.error or "") + "; server restarted mid-run"
            await _restore_and_finalize(db, settings, r)
        await db.commit()
        return len(rows)


# ----- internals ------------------------------------------------------


async def _drive_run(
    sessionmaker: async_sessionmaker, settings: Settings, run_id: int,
) -> None:
    """Background task body: step each participant until done."""
    global _active_task, _active_run_id
    client = build_client(settings)
    try:
        # Build per-participant ramp sequence once.
        async with sessionmaker() as db:
            run = await db.get(ToolRun, run_id)
            if run is None:
                return
            cfg = run.config
        radio = cfg["radio"]
        step_size_db = int(cfg["step_size_db"])
        step_interval_s = int(cfg["step_interval_s"])
        instant = bool(cfg.get("instant", False))
        queues: dict[str, list[str]] = {}
        for p in cfg["participants"]:
            start = p["start_value"]
            target = p["target_value"]
            if instant:
                # No ramp — single step straight to the target (or empty if
                # we're already there, in which case nothing to do).
                queues[p["ap_serial"]] = [] if start == target else [target]
            else:
                # Direction is informational; the ramp goes wherever target says.
                queues[p["ap_serial"]] = _ramp_sequence(
                    start, target, step_size_db,
                )

        # First tick fires immediately; the step_interval_s gap is enforced
        # AFTER each step is confirmed (not on a fixed clock). That means the
        # user gets the full interval between settled states, not racing
        # against how long Ruckus took to apply the change.
        while any(queues.values()):
            # Re-read run to honor cancellations + deadline.
            async with sessionmaker() as db:
                run = await db.get(ToolRun, run_id)
                if run is None or run.state != "running":
                    return
                if _now_ms() >= run.ends_at:
                    run.state = "completed"
                    run.error = "exceeded ends_at"
                    await _restore_and_finalize(db, settings, run)
                    await db.commit()
                    return

            for serial, q in list(queues.items()):
                if not q:
                    continue
                value = q.pop(0)
                ok, req_id, err = await _set_power_and_wait(
                    client, serial, radio, value,
                )
                async with sessionmaker() as db:
                    step = ToolRunStep(
                        run_id=run_id,
                        ts_ms=_now_ms(),
                        ap_serial=serial,
                        action={"radio": radio, "tx_power": value},
                        success=ok,
                        ruckus_request_id=req_id,
                        error=err,
                    )
                    db.add(step)
                    if not ok:
                        run = await db.get(ToolRun, run_id)
                        if run is not None:
                            run.state = "failed"
                            run.error = (
                                f"step failed on {serial}: {err or 'unknown'}"
                            )
                            await _restore_and_finalize(db, settings, run)
                    await db.commit()
                    if not ok:
                        return

            # All participants for this tick succeeded. Wait the interval
            # before the next tick — but skip the wait if we're already done.
            if any(queues.values()):
                await asyncio.sleep(step_interval_s)

        # All queues drained → all participants at their targets. Mark done.
        # For ramp runs we restore to pre-run powers; for instant runs we
        # leave the new setting in place (that's the whole point of instant).
        async with sessionmaker() as db:
            run = await db.get(ToolRun, run_id)
            if run is None or run.state != "running":
                return
            run.state = "completed"
            if instant:
                run.finalized_at = _now_ms()
            else:
                await _restore_and_finalize(db, settings, run)
            await db.commit()
    except asyncio.CancelledError:
        log.info("attenuator.run_cancelled run_id=%d", run_id)
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("attenuator.run_crashed run_id=%d", run_id)
        async with sessionmaker() as db:
            run = await db.get(ToolRun, run_id)
            if run is not None:
                run.state = "failed"
                run.error = f"{type(e).__name__}: {e}"
                await _restore_and_finalize(db, settings, run)
                await db.commit()
    finally:
        await client.aclose()
        _active_task = None
        _active_run_id = None


async def _set_power_and_wait(
    client: RuckusClient, serial: str, radio: str, value: str,
) -> tuple[bool, str | None, str | None]:
    try:
        req_id = await client.put_ap_tx_power(serial, radio, value)
    except Exception as e:  # noqa: BLE001
        return False, None, f"put failed: {e}"
    try:
        result = await client.wait_for_activity(
            req_id, timeout_s=STEP_ACTIVITY_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001
        return False, req_id, f"wait failed: {e}"
    if result.status in SUCCESS_STATUSES:
        return True, req_id, None
    return False, req_id, f"activity {result.status}: {result.error or ''}"


async def _restore_and_finalize(
    db: AsyncSession, settings: Settings, run: ToolRun,
) -> None:
    """Put the APs back to the txPower they had at run start. Logs each
    restore as a step row. Marks finalized_at; caller handles commit."""
    revert = (run.revert_state or {}).get("participants") or []
    if not revert:
        run.finalized_at = _now_ms()
        return
    client = build_client(settings)
    try:
        for entry in revert:
            serial = entry["ap_serial"]
            radio = entry["radio"]
            tx = entry["tx_power"]
            ok, req_id, err = await _set_power_and_wait(
                client, serial, radio, tx,
            )
            db.add(
                ToolRunStep(
                    run_id=run.id,
                    ts_ms=_now_ms(),
                    ap_serial=serial,
                    action={"radio": radio, "tx_power": tx, "restore": True},
                    success=ok,
                    ruckus_request_id=req_id,
                    error=err,
                )
            )
            if not ok:
                log.warning(
                    "attenuator.restore_failed serial=%s err=%s", serial, err,
                )
    finally:
        await client.aclose()
    run.finalized_at = _now_ms()


async def _boost_associated_agents(
    db: AsyncSession, radio: str, participants: list[dict],
) -> None:
    """Any agent whose latest wireless interface report shows it's currently
    on a BSSID belonging to a participating AP gets boosted for the run
    window. Best-effort — if we can't find a match we just skip."""
    from pulse_server.db.models import AccessPointBssid, AgentInterface
    ap_ids = [int(p["ap_id"]) for p in participants]
    if not ap_ids:
        return
    bssids = (
        await db.execute(
            select(AccessPointBssid.bssid).where(
                AccessPointBssid.access_point_id.in_(ap_ids)
            )
        )
    ).scalars().all()
    if not bssids:
        return
    ifaces = (
        await db.execute(
            select(AgentInterface).where(
                AgentInterface.bssid.in_(list(bssids))
            )
        )
    ).scalars().all()
    agent_ids = {int(i.agent_id) for i in ifaces}
    if not agent_ids:
        return
    # Use a duration that covers the run window with generous headroom.
    duration_s = 20 * 60  # 20 minutes — admin can extend mid-run from /agents
    for aid in agent_ids:
        try:
            await boost_service.start_or_extend(db, aid, duration_s)
        except Exception as e:  # noqa: BLE001
            log.warning("attenuator.boost_failed agent=%s err=%s", aid, e)
    # Caller commits.
    await meta_repo.bump(db, meta_repo.PEER_ASSIGNMENTS_VERSION)
