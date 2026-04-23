"""add deep_dive_sessions table

A deep-dive session is a time-bounded intensive monitoring window: for a user-selected
set of agents, the server bumps ping rate between those agents to 1 Hz, then at the
end aggregates the raw samples into a per-pair report. The raw samples still live in
`ping_samples_raw`; we just persist the window + subset + the computed report here.

Revision ID: 0006_deep_dive_sessions
Revises: 0005_wireless_and_access_points
Create Date: 2026-04-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_deep_dive_sessions"
down_revision: str | None = "0005_wireless_and_access_points"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deep_dive_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),  # running | completed | cancelled
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


def downgrade() -> None:
    op.drop_index("ix_deep_dive_sessions_state_ends_at", table_name="deep_dive_sessions")
    op.drop_table("deep_dive_sessions")
