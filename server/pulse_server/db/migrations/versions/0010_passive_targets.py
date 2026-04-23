"""passive targets (ping-only devices with no agent)

Adds `passive_targets` plus a parallel data pipeline (raw samples, minute + hour
aggregates, link states) mirroring the existing agent-to-agent mesh. Keeping the
schemas separate avoids nullable columns on hot tables and keeps retention policies
decoupled if we ever diverge them.

Revision ID: 0010_passive_targets
Revises: 0009_access_point_bssids
Create Date: 2026-04-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_passive_targets"
down_revision: str | None = "0009_access_point_bssids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "passive_targets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False, unique=True),
        sa.Column("notes", sa.String(512), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
    )
    op.create_index(
        "ix_passive_targets_enabled", "passive_targets", ["enabled"]
    )

    op.create_table(
        "passive_ping_samples_raw",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_agent_id", sa.Integer, nullable=False),
        sa.Column("passive_target_id", sa.Integer, nullable=False),
        sa.Column("ts_ms", sa.Integer, nullable=False),
        sa.Column("rtt_ms", sa.Float, nullable=True),
        sa.Column("lost", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("seq", sa.Integer, nullable=True),
    )
    op.create_index(
        "ix_passive_raw_src_tgt_ts",
        "passive_ping_samples_raw",
        ["source_agent_id", "passive_target_id", "ts_ms"],
    )
    op.create_index("ix_passive_raw_ts", "passive_ping_samples_raw", ["ts_ms"])

    for tbl in ("passive_ping_aggregates_minute", "passive_ping_aggregates_hour"):
        op.create_table(
            tbl,
            sa.Column("source_agent_id", sa.Integer, primary_key=True),
            sa.Column("passive_target_id", sa.Integer, primary_key=True),
            sa.Column("bucket_ts_ms", sa.Integer, primary_key=True),
            sa.Column("sent", sa.Integer, nullable=False),
            sa.Column("lost", sa.Integer, nullable=False),
            sa.Column("rtt_avg", sa.Float, nullable=True),
            sa.Column("rtt_min", sa.Float, nullable=True),
            sa.Column("rtt_max", sa.Float, nullable=True),
            sa.Column("rtt_p50", sa.Float, nullable=True),
            sa.Column("rtt_p95", sa.Float, nullable=True),
            sa.Column("rtt_p99", sa.Float, nullable=True),
            sa.Column("jitter_ms", sa.Float, nullable=True),
        )

    op.create_table(
        "passive_link_states",
        sa.Column("source_agent_id", sa.Integer, primary_key=True),
        sa.Column("passive_target_id", sa.Integer, primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("since_ts", sa.Integer, nullable=False),
        sa.Column("loss_pct_1m", sa.Float, nullable=True),
        sa.Column("rtt_p95_1m", sa.Float, nullable=True),
        sa.Column("candidate_state", sa.String(16), nullable=True),
        sa.Column("candidate_since_ts", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("passive_link_states")
    op.drop_table("passive_ping_aggregates_hour")
    op.drop_table("passive_ping_aggregates_minute")
    op.drop_index("ix_passive_raw_ts", table_name="passive_ping_samples_raw")
    op.drop_index("ix_passive_raw_src_tgt_ts", table_name="passive_ping_samples_raw")
    op.drop_table("passive_ping_samples_raw")
    op.drop_index("ix_passive_targets_enabled", table_name="passive_targets")
    op.drop_table("passive_targets")
