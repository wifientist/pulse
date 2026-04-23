"""replace deep-dive sessions with per-agent boost + add p50/p99 to aggregates

The old deep_dive_sessions model is superseded by the Trends view. Boost becomes a
lightweight per-agent toggle: while an agent has an unexpired boost row, its outbound
pings run at 1 Hz. Finer-grained statistics show up via the Trends page's raw tier.

Also bumps the minute + hour aggregate schemas to record p50 and p99 so Trends can
show a proper latency distribution in tooltips (p95 was already stored).

Revision ID: 0008_boost_and_percentiles
Revises: 0007_wireless_samples
Create Date: 2026-04-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_boost_and_percentiles"
down_revision: str | None = "0007_wireless_samples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop the old deep-dive sessions table (reports no longer kept).
    op.drop_table("deep_dive_sessions")

    # 2. agent_boosts — one row per active boost, simple upsert on re-boost.
    op.create_table(
        "agent_boosts",
        sa.Column("agent_id", sa.Integer, primary_key=True),
        sa.Column("started_at", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.Integer, nullable=False),
    )
    op.create_index(
        "ix_agent_boosts_expires_at", "agent_boosts", ["expires_at"]
    )

    # 3. p50 + p99 on minute aggregates (nullable — backfilled by future rollups).
    with op.batch_alter_table("ping_aggregates_minute") as batch:
        batch.add_column(sa.Column("rtt_p50", sa.Float, nullable=True))
        batch.add_column(sa.Column("rtt_p99", sa.Float, nullable=True))

    # 4. Same on hour aggregates.
    with op.batch_alter_table("ping_aggregates_hour") as batch:
        batch.add_column(sa.Column("rtt_p50", sa.Float, nullable=True))
        batch.add_column(sa.Column("rtt_p99", sa.Float, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ping_aggregates_hour") as batch:
        batch.drop_column("rtt_p99")
        batch.drop_column("rtt_p50")
    with op.batch_alter_table("ping_aggregates_minute") as batch:
        batch.drop_column("rtt_p99")
        batch.drop_column("rtt_p50")

    op.drop_index("ix_agent_boosts_expires_at", table_name="agent_boosts")
    op.drop_table("agent_boosts")

    # Re-create deep_dive_sessions (matches 0006 shape).
    op.create_table(
        "deep_dive_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("agent_ids", sa.JSON, nullable=False),
        sa.Column("started_at", sa.Integer, nullable=False),
        sa.Column("ends_at", sa.Integer, nullable=False),
        sa.Column("finalized_at", sa.Integer, nullable=True),
        sa.Column("report", sa.JSON, nullable=True),
    )
    op.create_index(
        "ix_deep_dive_sessions_state_ends_at",
        "deep_dive_sessions",
        ["state", "ends_at"],
    )
