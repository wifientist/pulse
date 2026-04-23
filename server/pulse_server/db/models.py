"""SQLAlchemy 2.0 declarative models for Pulse.

Design notes:
- All timestamps are unix epoch milliseconds stored as INTEGER. This keeps indexes small
  and comparisons cheap on SQLite.
- Tokens (enrollment + agent) are persisted as argon2 hashes. The plaintext only exists
  in memory during issuance and in agent-local storage.
- JSON payload columns use SQLAlchemy's generic JSON type which maps to SQLite's TEXT
  via json.dumps/loads.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_uid: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    hostname: Mapped[str] = mapped_column(String(255))
    os: Mapped[str] = mapped_column(String(32))
    platform_caps: Mapped[dict] = mapped_column(JSON, default=dict)
    primary_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    """Agent-reported test IP; this is the address other agents will ping."""
    management_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    """Server-observed TCP source IP of the agent's poll connection. Populated on every
    poll, not reported by the agent. Useful when mgmt and test interfaces are separate."""
    cidr: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    token_hash: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), index=True)
    poll_interval_s: Mapped[int] = mapped_column(Integer, default=5)
    ping_interval_s: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[int] = mapped_column(Integer)
    approved_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_poll_at: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    agent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AgentInterface(Base):
    """Per-agent network interface, keyed by MAC.

    Agents enumerate their own interfaces (via psutil) on every poll and ship the list
    up. MAC is the stable identifier across DHCP changes; `current_ip` is whatever that
    MAC currently holds. `role` is admin-assigned: `test` interfaces have their IP
    snapshotted into peer_assignments.target_ip for inbound pings; `management` and
    `ignored` are informational; `unknown` is the default until admin classifies.
    """

    __tablename__ = "agent_interfaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    mac: Mapped[str] = mapped_column(String(17))
    current_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    iface_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="unknown")
    first_seen: Mapped[int] = mapped_column(Integer)
    last_seen: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("agent_id", "mac", name="uq_agent_interfaces_agent_mac"),
    )


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    label: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class PendingEnrollment(Base):
    __tablename__ = "pending_enrollments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_uid_candidate: Mapped[str] = mapped_column(String(36), unique=True)
    enrollment_token_id: Mapped[int] = mapped_column(ForeignKey("enrollment_tokens.id"))
    reported_hostname: Mapped[str] = mapped_column(String(255))
    reported_ip: Mapped[str] = mapped_column(String(45))
    caps: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(Integer)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    handoff_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    """One-shot plaintext of the per-agent bearer token, set at approval and cleared the
    first time the agent fetches it via /v1/enroll/poll. Plaintext lives in the DB only
    for the window between admin approval and the next agent poll (seconds in practice).
    The corresponding argon2 hash is stored on the Agent row and is the permanent copy."""


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer)


class AgentGroup(Base):
    __tablename__ = "agent_groups"

    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), primary_key=True)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_tags_key_value", "key", "value"),
        UniqueConstraint("agent_id", "key", name="uq_tag_agent_key"),
    )


class PeerAssignment(Base):
    __tablename__ = "peer_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    target_agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    target_ip: Mapped[str] = mapped_column(String(45))
    interval_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(8), default="auto")  # auto | manual

    __table_args__ = (
        UniqueConstraint("source_agent_id", "target_agent_id", name="uq_peer_pair"),
    )


class PingSampleRaw(Base):
    __tablename__ = "ping_samples_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_agent_id: Mapped[int] = mapped_column(Integer)
    target_agent_id: Mapped[int] = mapped_column(Integer)
    ts_ms: Mapped[int] = mapped_column(Integer)
    rtt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    lost: Mapped[bool] = mapped_column(Boolean, default=False)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_ping_raw_src_tgt_ts", "source_agent_id", "target_agent_id", "ts_ms"),
        Index("ix_ping_raw_ts", "ts_ms"),
    )


class PingAggregateMinute(Base):
    __tablename__ = "ping_aggregates_minute"

    source_agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_ts_ms: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent: Mapped[int] = mapped_column(Integer)
    lost: Mapped[int] = mapped_column(Integer)
    rtt_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_p95: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class PingAggregateHour(Base):
    __tablename__ = "ping_aggregates_hour"

    source_agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_ts_ms: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent: Mapped[int] = mapped_column(Integer)
    lost: Mapped[int] = mapped_column(Integer)
    rtt_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_p95: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class Command(Base):
    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    lease_expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer)
    dispatched_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline_ms: Mapped[int] = mapped_column(Integer)
    test_run_id: Mapped[int | None] = mapped_column(ForeignKey("tests.id"), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_commands_agent_status_created", "agent_id", "status", "created_at"),
        Index("ix_commands_status_lease", "status", "lease_expires_at"),
    )


class CommandResult(Base):
    __tablename__ = "command_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    command_id: Mapped[int] = mapped_column(ForeignKey("commands.id"), unique=True)
    success: Mapped[bool] = mapped_column(Boolean)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[int] = mapped_column(Integer)


class Test(Base):
    __tablename__ = "tests"
    __test__ = False  # tell pytest this ORM class isn't a test class

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    initiated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    created_at: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finished_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class IperfSession(Base):
    __tablename__ = "iperf_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    server_agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    client_agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    server_port: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32), index=True)
    server_started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finished_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    watchdog_deadline: Mapped[int] = mapped_column(Integer, index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LinkStateRow(Base):
    __tablename__ = "link_states"

    source_agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    since_ts: Mapped[int] = mapped_column(Integer)
    loss_pct_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_p95_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    candidate_since_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_agent_id: Mapped[int] = mapped_column(Integer)
    target_agent_id: Mapped[int] = mapped_column(Integer)
    from_state: Mapped[str] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16))
    at_ts: Mapped[int] = mapped_column(Integer, index=True)
    context: Mapped[dict] = mapped_column(JSON, default=dict)


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(Text)
    secret: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    event_filter: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(Integer)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    webhook_id: Mapped[int] = mapped_column(ForeignKey("webhooks.id"))
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[int] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="pending")

    __table_args__ = (
        Index("ix_wh_deliveries_state_next", "state", "next_attempt_at"),
    )


class Meta(Base):
    """Key-value bag for singleton runtime state: peer_assignments_version,
    last_minute_bucket_rolled, last_hour_bucket_rolled, etc."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
