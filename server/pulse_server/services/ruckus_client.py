"""Thin async wrapper around the Ruckus One REST API.

Scope: just the surface the Attenuator tool needs — OAuth2 client-credentials
auth with JWT cache, AP listing, and per-AP radio settings get/put. PUTs are
async on Ruckus's side (202 + requestId); we poll /activities until terminal.

No global singleton — the caller gets one via `build_client()` which reads
the pulse Settings. A single client instance is safe to share across requests
(it serializes token refreshes under a lock and reuses one httpx.AsyncClient).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from pulse_server.config import Settings

log = logging.getLogger(__name__)


# Radio sub-key in the radioSettings payload for each band.
RADIO_KEYS = {
    "24g": "apRadioParams24G",
    "5g": "apRadioParams50G",
    "6g": "apRadioParams6G",
}

# Valid Ruckus txPower values, in descending attenuation order. The OpenAPI
# spec publishes only -1..-10 + MAX/MIN/Auto, but the live API and Ruckus One
# UI both accept at least -1..-23 in practice (observed via browser dev tools
# on real PUTs). We expose the wider range; Ruckus will 422 on any value a
# specific AP model doesn't support, which surfaces as a normal step failure.
TX_POWER_VALUES = [
    "MAX",
    *[f"-{i}" for i in range(1, 24)],
    "MIN",
]
TX_POWER_SYMBOLIC = {"Auto", "MAX", "MIN"}


def is_valid_tx_power(v: str) -> bool:
    return v == "Auto" or v in TX_POWER_VALUES


class RuckusApiError(Exception):
    """Raised on non-recoverable API errors (bad auth, 4xx/5xx). Transient
    network errors bubble up as httpx exceptions — callers decide whether to
    retry."""


@dataclass
class ActivityResult:
    request_id: str
    status: str  # PENDING | INPROGRESS | SUCCESS | FAIL | CANCELLED | PARTIAL_SUCCESS | SKIPPED
    error: str | None
    terminal: bool


TERMINAL_STATUSES = {"SUCCESS", "FAIL", "CANCELLED", "PARTIAL_SUCCESS", "SKIPPED"}
SUCCESS_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS"}


class RuckusClient:
    """One client per app; safe to share across concurrent callers."""

    def __init__(self, settings: Settings) -> None:
        if not settings.ruckus_configured:
            raise RuntimeError(
                "Ruckus One not configured — set PULSE_RUCKUS_TENANT_ID, "
                "PULSE_RUCKUS_CLIENT_ID, PULSE_RUCKUS_CLIENT_SECRET, "
                "PULSE_RUCKUS_VENUE_ID in .env"
            )
        self._settings = settings
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0  # monotonic epoch seconds
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- auth -----------------------------------------------------------

    async def _authenticate(self) -> None:
        """Exchange client credentials for a JWT. Called lazily; caches the
        token until 60s before its expiry."""
        s = self._settings
        url = f"{s.ruckus_auth_base}/oauth2/token/{s.ruckus_tenant_id}"
        r = await self._http.post(
            url,
            headers={"content-type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": s.ruckus_client_id,
                "client_secret": s.ruckus_client_secret,
            },
        )
        if r.status_code != 200:
            raise RuckusApiError(
                f"auth failed: {r.status_code} {r.text[:200]}"
            )
        body = r.json()
        token = body.get("access_token")
        expires_in = int(body.get("expires_in", 3600))
        if not token:
            raise RuckusApiError(f"auth response missing access_token: {body}")
        self._token = token
        # Refresh 60s before actual expiry to avoid racing a mid-request expiry.
        self._token_expires_at = time.monotonic() + max(60, expires_in - 60)
        log.info(
            "ruckus.auth_ok expires_in=%ds (refresh_in=%ds)",
            expires_in,
            max(60, expires_in - 60),
        )

    async def _ensure_token(self) -> str:
        async with self._token_lock:
            if (
                self._token is None
                or time.monotonic() >= self._token_expires_at
            ):
                await self._authenticate()
            assert self._token is not None
            return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        query: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """One retry on 401 (token may have been revoked mid-flight)."""
        for attempt in (1, 2):
            token = await self._ensure_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            if extra_headers:
                headers.update(extra_headers)
            url = f"{self._settings.ruckus_api_base}{path}"
            r = await self._http.request(
                method, url, json=json_body, params=query, headers=headers,
            )
            if r.status_code == 401 and attempt == 1:
                self._token = None
                self._token_expires_at = 0.0
                continue
            return r
        return r  # pragma: no cover — unreachable; loop always returns

    # --- AP inventory ---------------------------------------------------

    async def list_aps(self, *, page_size: int = 500) -> list[dict]:
        """POST /venues/aps/query — returns a list of venue APs.
        The response shape varies by account/version; caller should treat
        fields defensively (name, serialNumber, model, status, etc.)."""
        r = await self._request(
            "POST",
            "/venues/aps/query",
            json_body={"pageSize": page_size},
        )
        if r.status_code >= 300:
            raise RuckusApiError(f"list_aps {r.status_code}: {r.text[:400]}")
        body = r.json()
        # Response is typically a dict with `data` or `content` or similar.
        # Try common keys; fall through to raw list if it came back as one.
        for k in ("data", "content", "results", "items"):
            if isinstance(body, dict) and k in body and isinstance(body[k], list):
                return body[k]
        if isinstance(body, list):
            return body
        raise RuckusApiError(
            f"list_aps: unexpected response shape: {type(body).__name__}"
        )

    # --- Radio settings -------------------------------------------------

    async def get_ap_radio(self, serial: str) -> dict:
        venue = self._settings.ruckus_venue_id
        r = await self._request(
            "GET",
            f"/venues/{venue}/aps/{serial}/radioSettings",
            extra_headers={"Accept": "application/vnd.ruckus.v1.1+json"},
        )
        if r.status_code >= 300:
            raise RuckusApiError(
                f"get_ap_radio {serial} {r.status_code}: {r.text[:400]}"
            )
        return r.json()

    async def put_ap_radio(self, serial: str, body: dict) -> str:
        """Returns the Ruckus request_id for polling. Mutation is async."""
        venue = self._settings.ruckus_venue_id
        r = await self._request(
            "PUT",
            f"/venues/{venue}/aps/{serial}/radioSettings",
            json_body=body,
            extra_headers={
                "Content-Type": "application/vnd.ruckus.v1.1+json",
                "Accept": "application/vnd.ruckus.v1.1+json",
            },
        )
        if r.status_code not in (200, 202):
            raise RuckusApiError(
                f"put_ap_radio {serial} {r.status_code}: {r.text[:400]}"
            )
        data = r.json() if r.text else {}
        rid = data.get("requestId")
        if not rid:
            raise RuckusApiError(
                f"put_ap_radio {serial}: response missing requestId: {data}"
            )
        return rid

    async def put_ap_tx_power(
        self, serial: str, radio: str, tx_power: str,
    ) -> str:
        """Convenience — fetch current settings, patch only txPower for the
        requested radio, PUT back. Preserves everything else (channel method,
        bandwidth, etc.) because the API requires a full-object PUT."""
        if radio not in RADIO_KEYS:
            raise ValueError(f"unknown radio: {radio} (want one of {list(RADIO_KEYS)})")
        if not is_valid_tx_power(tx_power):
            raise ValueError(f"invalid txPower: {tx_power}")
        current = await self.get_ap_radio(serial)
        key = RADIO_KEYS[radio]
        sub = dict(current.get(key) or {})
        sub["txPower"] = tx_power
        # Safety: if the AP was set to inherit from venue/group, stop
        # inheriting now so our write sticks. Only flip when already true —
        # leave user-chosen `false` alone.
        if sub.get("useVenueSettings") is True:
            sub["useVenueSettings"] = False
        if sub.get("useVenueOrApGroupSettings") is True:
            sub["useVenueOrApGroupSettings"] = False
        body = {**current, key: sub}
        return await self.put_ap_radio(serial, body)

    # --- Activities (async-mutation polling) ---------------------------

    async def get_activity(self, request_id: str) -> ActivityResult | None:
        """Returns None if Ruckus says the activity doesn't exist (yet)
        rather than raising. There's a race window between the PUT 202 and
        the activity record becoming queryable, and some short activities
        seem to be GC'd before we can poll. Caller decides how to treat
        None."""
        r = await self._request("GET", f"/activities/{request_id}")
        if r.status_code == 404:
            return None
        if r.status_code >= 300:
            raise RuckusApiError(
                f"get_activity {request_id} {r.status_code}: {r.text[:400]}"
            )
        body = r.json()
        status = (body.get("status") or "").upper()
        return ActivityResult(
            request_id=request_id,
            status=status,
            error=body.get("error") or None,
            terminal=status in TERMINAL_STATUSES,
        )

    async def wait_for_activity(
        self,
        request_id: str,
        *,
        timeout_s: float = 30.0,
        poll_interval_s: float = 1.0,
        initial_delay_s: float = 1.0,
    ) -> ActivityResult:
        """Poll /activities/{id} until terminal or timeout. If we ONLY ever
        get 404 during the window, assume the activity completed quickly
        (the preceding PUT was already 202 Accepted); return a synthetic
        SUCCESS marked with a warning note. Any actual terminal status
        returned along the way takes precedence."""
        if initial_delay_s > 0:
            await asyncio.sleep(initial_delay_s)
        deadline = time.monotonic() + timeout_s
        last: ActivityResult | None = None
        saw_any_non_404 = False
        while True:
            result = await self.get_activity(request_id)
            if result is not None:
                saw_any_non_404 = True
                last = result
                if result.terminal:
                    return result
            if time.monotonic() >= deadline:
                if saw_any_non_404 and last is not None:
                    return ActivityResult(
                        request_id=request_id,
                        status=last.status or "TIMEOUT",
                        error=f"timed out after {timeout_s}s",
                        terminal=True,
                    )
                # Only ever saw 404s — treat as presumed success. The PUT
                # itself was accepted; Ruckus just never exposed an
                # activity for us to confirm. Log upstream if this becomes
                # a frequent false-positive.
                log.info(
                    "ruckus.activity_invisible request_id=%s — treating as success",
                    request_id,
                )
                return ActivityResult(
                    request_id=request_id,
                    status="SUCCESS",
                    error=None,
                    terminal=True,
                )
            await asyncio.sleep(poll_interval_s)


def build_client(settings: Settings) -> RuckusClient:
    return RuckusClient(settings)
