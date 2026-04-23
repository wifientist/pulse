"""Pydantic DTOs exchanged between agent and server.

These are the canonical wire contracts. Keep them minimal and boring. Do not import ORM
models or server-only code from here — the agent depends on this module too.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from pulse_shared.enums import CommandType


class AgentCaps(BaseModel):
    """Agent-reported platform capabilities, surfaced at enroll and every poll."""

    os: str = Field(description="linux | windows | darwin | other")
    platform_tag: str | None = None
    raw_icmp: bool = False
    container: bool = False
    iperf3_available: bool = False
    agent_version: str = "0.1.0"
    protocol_version: str


class EnrollRequest(BaseModel):
    enrollment_token: str
    hostname: str
    reported_ip: str
    caps: AgentCaps


class EnrollResponse(BaseModel):
    agent_uid: str
    pending: bool = True


class EnrollPollRequest(BaseModel):
    enrollment_token: str
    agent_uid: str


class EnrollPollResponse(BaseModel):
    approved: bool
    agent_token: str | None = None
    """Only populated on the first approved poll. Agent must persist it immediately."""


class PingSample(BaseModel):
    """One ping sample from source to target.

    `target_agent_uid` is authoritative; IP changes are handled server-side.
    `ts_ms` is the agent-local epoch millisecond when the probe completed. The server may
    rewrite this for bucket placement to account for clock skew.
    """

    target_agent_uid: str
    ts_ms: int
    rtt_ms: float | None = None
    lost: bool = False
    seq: int | None = None


class CommandResult(BaseModel):
    command_id: int
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class AgentInterface(BaseModel):
    """One network interface on the agent's host, reported on every poll.

    MAC is the stable identifier (doesn't change across reboots or DHCP renewals).
    `ip` is the current IPv4 address the interface holds — may be None if the interface
    is down. `iface_name` is informational (e.g. `eth1`, `ens20`).
    """

    mac: str
    ip: str | None = None
    iface_name: str | None = None


class PollRequest(BaseModel):
    agent_uid: str
    now_ms: int
    caps: AgentCaps
    primary_ip: str
    ping_samples: list[PingSample] = Field(default_factory=list)
    command_results: list[CommandResult] = Field(default_factory=list)
    peers_version_seen: int = 0
    dropped_samples_since_last: int = 0
    # Optional so older agents (pre-0.2.0) keep working — they just won't get MAC-
    # tracking benefits until they're upgraded.
    interfaces: list[AgentInterface] = Field(default_factory=list)


class PeerAssignment(BaseModel):
    target_agent_uid: str
    target_ip: str
    interval_s: int
    enabled: bool = True


class Command(BaseModel):
    id: int
    type: CommandType
    payload: dict[str, Any]
    deadline_ms: int


class AgentConfig(BaseModel):
    poll_interval_s: int
    ping_interval_s: int


class PollResponse(BaseModel):
    server_time_ms: int
    config: AgentConfig
    peer_assignments_version: int
    peer_assignments: list[PeerAssignment] | None = None
    """Present only when the agent's reported `peers_version_seen` differs from the server's."""
    commands: list[Command] = Field(default_factory=list)


# -- Probe payloads / results --------------------------------------------------


class TcpProbeSpec(BaseModel):
    host: str
    port: int
    count: int = 1
    timeout_s: float = 2.0


class TcpProbeResult(BaseModel):
    attempts: int
    successes: int
    rtt_ms_avg: float | None
    error: str | None = None


class DnsProbeSpec(BaseModel):
    name: str
    record_type: str = "A"
    resolver: str | None = None
    timeout_s: float = 2.0


class DnsProbeResult(BaseModel):
    addresses: list[str]
    duration_ms: float
    error: str | None = None


class HttpProbeSpec(BaseModel):
    url: str
    method: str = "GET"
    timeout_s: float = 5.0
    expect_status: int | None = None


class HttpProbeResult(BaseModel):
    status: int | None
    ttfb_ms: float | None
    total_ms: float | None
    error: str | None = None


class Iperf3ServerStartSpec(BaseModel):
    session_id: int
    port: int
    bind: str = "0.0.0.0"
    one_shot: bool = True


class Iperf3ServerStartResult(BaseModel):
    listening: bool
    port: int
    error: str | None = None


class Iperf3ClientSpec(BaseModel):
    session_id: int
    host: str
    port: int
    duration_s: int = 10
    protocol: str = "tcp"  # tcp | udp
    bitrate: str | None = None  # e.g. "100M" for udp


class Iperf3ClientResult(BaseModel):
    throughput_bps: float | None
    retransmits: int | None = None
    duration_s: float | None = None
    raw_json: dict[str, Any] | None = None
    error: str | None = None
