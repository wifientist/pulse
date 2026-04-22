"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-21

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("agent_uid", sa.String(36), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("os", sa.String(32), nullable=False),
        sa.Column("platform_caps", sa.JSON, nullable=False),
        sa.Column("primary_ip", sa.String(45)),
        sa.Column("cidr", sa.String(64)),
        sa.Column("token_hash", sa.Text, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("poll_interval_s", sa.Integer, nullable=False),
        sa.Column("ping_interval_s", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("approved_at", sa.Integer),
        sa.Column("last_poll_at", sa.Integer),
        sa.Column("agent_version", sa.String(32)),
    )
    op.create_index("ix_agents_agent_uid", "agents", ["agent_uid"], unique=True)
    op.create_index("ix_agents_state", "agents", ["state"])
    op.create_index("ix_agents_last_poll_at", "agents", ["last_poll_at"])
    op.create_index("ix_agents_cidr", "agents", ["cidr"])

    op.create_table(
        "enrollment_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.Text, nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.Integer),
        sa.Column("uses_remaining", sa.Integer),
        sa.Column("revoked", sa.Boolean, nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_enrollment_token_hash"),
    )

    op.create_table(
        "pending_enrollments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("agent_uid_candidate", sa.String(36), nullable=False),
        sa.Column("enrollment_token_id", sa.Integer, nullable=False),
        sa.Column("reported_hostname", sa.String(255), nullable=False),
        sa.Column("reported_ip", sa.String(45), nullable=False),
        sa.Column("caps", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("approved", sa.Boolean, nullable=False),
        sa.Column("handoff_token", sa.Text),
        sa.ForeignKeyConstraint(["enrollment_token_id"], ["enrollment_tokens.id"]),
        sa.UniqueConstraint("agent_uid_candidate", name="uq_pending_enrollment_uid"),
    )

    op.create_table(
        "groups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.UniqueConstraint("name", name="uq_group_name"),
    )

    op.create_table(
        "agent_groups",
        sa.Column("agent_id", sa.Integer, nullable=False),
        sa.Column("group_id", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"]),
        sa.PrimaryKeyConstraint("agent_id", "group_id"),
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("agent_id", sa.Integer, nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.String(256), nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.UniqueConstraint("agent_id", "key", name="uq_tag_agent_key"),
    )
    op.create_index("ix_tags_agent_id", "tags", ["agent_id"])
    op.create_index("ix_tags_key_value", "tags", ["key", "value"])

    op.create_table(
        "peer_assignments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_agent_id", sa.Integer, nullable=False),
        sa.Column("target_agent_id", sa.Integer, nullable=False),
        sa.Column("target_ip", sa.String(45), nullable=False),
        sa.Column("interval_s", sa.Integer),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.ForeignKeyConstraint(["source_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["target_agent_id"], ["agents.id"]),
        sa.UniqueConstraint("source_agent_id", "target_agent_id", name="uq_peer_pair"),
    )
    op.create_index("ix_peer_assignments_source_agent_id", "peer_assignments", ["source_agent_id"])

    op.create_table(
        "ping_samples_raw",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_agent_id", sa.Integer, nullable=False),
        sa.Column("target_agent_id", sa.Integer, nullable=False),
        sa.Column("ts_ms", sa.Integer, nullable=False),
        sa.Column("rtt_ms", sa.Float),
        sa.Column("lost", sa.Boolean, nullable=False),
        sa.Column("seq", sa.Integer),
    )
    op.create_index(
        "ix_ping_raw_src_tgt_ts",
        "ping_samples_raw",
        ["source_agent_id", "target_agent_id", "ts_ms"],
    )
    op.create_index("ix_ping_raw_ts", "ping_samples_raw", ["ts_ms"])

    op.create_table(
        "ping_aggregates_minute",
        sa.Column("source_agent_id", sa.Integer, nullable=False),
        sa.Column("target_agent_id", sa.Integer, nullable=False),
        sa.Column("bucket_ts_ms", sa.Integer, nullable=False),
        sa.Column("sent", sa.Integer, nullable=False),
        sa.Column("lost", sa.Integer, nullable=False),
        sa.Column("rtt_avg", sa.Float),
        sa.Column("rtt_min", sa.Float),
        sa.Column("rtt_max", sa.Float),
        sa.Column("rtt_p95", sa.Float),
        sa.Column("jitter_ms", sa.Float),
        sa.PrimaryKeyConstraint("source_agent_id", "target_agent_id", "bucket_ts_ms"),
    )

    op.create_table(
        "ping_aggregates_hour",
        sa.Column("source_agent_id", sa.Integer, nullable=False),
        sa.Column("target_agent_id", sa.Integer, nullable=False),
        sa.Column("bucket_ts_ms", sa.Integer, nullable=False),
        sa.Column("sent", sa.Integer, nullable=False),
        sa.Column("lost", sa.Integer, nullable=False),
        sa.Column("rtt_avg", sa.Float),
        sa.Column("rtt_min", sa.Float),
        sa.Column("rtt_max", sa.Float),
        sa.Column("rtt_p95", sa.Float),
        sa.Column("jitter_ms", sa.Float),
        sa.PrimaryKeyConstraint("source_agent_id", "target_agent_id", "bucket_ts_ms"),
    )

    op.create_table(
        "tests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("initiated_by", sa.String(64)),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("spec", sa.JSON, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("started_at", sa.Integer),
        sa.Column("finished_at", sa.Integer),
        sa.Column("result", sa.JSON),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_tests_state", "tests", ["state"])

    op.create_table(
        "commands",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("agent_id", sa.Integer, nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("lease_expires_at", sa.Integer),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("dispatched_at", sa.Integer),
        sa.Column("deadline_ms", sa.Integer, nullable=False),
        sa.Column("test_run_id", sa.Integer),
        sa.Column("idempotency_key", sa.String(64)),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["test_run_id"], ["tests.id"]),
    )
    op.create_index(
        "ix_commands_agent_status_created", "commands", ["agent_id", "status", "created_at"]
    )
    op.create_index("ix_commands_status_lease", "commands", ["status", "lease_expires_at"])

    op.create_table(
        "command_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("command_id", sa.Integer, nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("result", sa.JSON),
        sa.Column("error", sa.Text),
        sa.Column("received_at", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(["command_id"], ["commands.id"]),
        sa.UniqueConstraint("command_id", name="uq_command_result"),
    )

    op.create_table(
        "iperf_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("test_id", sa.Integer, nullable=False),
        sa.Column("server_agent_id", sa.Integer, nullable=False),
        sa.Column("client_agent_id", sa.Integer, nullable=False),
        sa.Column("server_port", sa.Integer, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("server_started_at", sa.Integer),
        sa.Column("client_started_at", sa.Integer),
        sa.Column("finished_at", sa.Integer),
        sa.Column("watchdog_deadline", sa.Integer, nullable=False),
        sa.Column("result", sa.JSON),
        sa.Column("error", sa.Text),
        sa.ForeignKeyConstraint(["test_id"], ["tests.id"]),
        sa.ForeignKeyConstraint(["server_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["client_agent_id"], ["agents.id"]),
    )
    op.create_index("ix_iperf_sessions_test_id", "iperf_sessions", ["test_id"])
    op.create_index("ix_iperf_sessions_state", "iperf_sessions", ["state"])
    op.create_index(
        "ix_iperf_sessions_watchdog_deadline", "iperf_sessions", ["watchdog_deadline"]
    )

    op.create_table(
        "link_states",
        sa.Column("source_agent_id", sa.Integer, nullable=False),
        sa.Column("target_agent_id", sa.Integer, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("since_ts", sa.Integer, nullable=False),
        sa.Column("loss_pct_1m", sa.Float),
        sa.Column("rtt_p95_1m", sa.Float),
        sa.Column("candidate_state", sa.String(16)),
        sa.Column("candidate_since_ts", sa.Integer),
        sa.PrimaryKeyConstraint("source_agent_id", "target_agent_id"),
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_agent_id", sa.Integer, nullable=False),
        sa.Column("target_agent_id", sa.Integer, nullable=False),
        sa.Column("from_state", sa.String(16), nullable=False),
        sa.Column("to_state", sa.String(16), nullable=False),
        sa.Column("at_ts", sa.Integer, nullable=False),
        sa.Column("context", sa.JSON, nullable=False),
    )
    op.create_index("ix_alerts_at_ts", "alerts", ["at_ts"])

    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("secret", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("event_filter", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False),
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("webhook_id", sa.Integer, nullable=False),
        sa.Column("alert_id", sa.Integer),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("next_attempt_at", sa.Integer, nullable=False),
        sa.Column("last_error", sa.Text),
        sa.Column("delivered_at", sa.Integer),
        sa.Column("state", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"]),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"]),
    )
    op.create_index(
        "ix_wh_deliveries_state_next", "webhook_deliveries", ["state", "next_attempt_at"]
    )

    op.create_table(
        "meta",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("meta")
    op.drop_index("ix_wh_deliveries_state_next", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
    op.drop_index("ix_alerts_at_ts", table_name="alerts")
    op.drop_table("alerts")
    op.drop_table("link_states")
    op.drop_index("ix_iperf_sessions_watchdog_deadline", table_name="iperf_sessions")
    op.drop_index("ix_iperf_sessions_state", table_name="iperf_sessions")
    op.drop_index("ix_iperf_sessions_test_id", table_name="iperf_sessions")
    op.drop_table("iperf_sessions")
    op.drop_table("command_results")
    op.drop_index("ix_commands_status_lease", table_name="commands")
    op.drop_index("ix_commands_agent_status_created", table_name="commands")
    op.drop_table("commands")
    op.drop_index("ix_tests_state", table_name="tests")
    op.drop_table("tests")
    op.drop_table("ping_aggregates_hour")
    op.drop_table("ping_aggregates_minute")
    op.drop_index("ix_ping_raw_ts", table_name="ping_samples_raw")
    op.drop_index("ix_ping_raw_src_tgt_ts", table_name="ping_samples_raw")
    op.drop_table("ping_samples_raw")
    op.drop_index("ix_peer_assignments_source_agent_id", table_name="peer_assignments")
    op.drop_table("peer_assignments")
    op.drop_index("ix_tags_key_value", table_name="tags")
    op.drop_index("ix_tags_agent_id", table_name="tags")
    op.drop_table("tags")
    op.drop_table("agent_groups")
    op.drop_table("groups")
    op.drop_table("pending_enrollments")
    op.drop_table("enrollment_tokens")
    op.drop_index("ix_agents_cidr", table_name="agents")
    op.drop_index("ix_agents_last_poll_at", table_name="agents")
    op.drop_index("ix_agents_state", table_name="agents")
    op.drop_index("ix_agents_agent_uid", table_name="agents")
    op.drop_table("agents")
