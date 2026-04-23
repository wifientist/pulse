"""add wireless_samples table

One row per poll per wireless interface the agent reports. Used by deep-dive report
aggregation (signal min/max/avg/stddev + roam detection) and potentially by future
historical views. Pruned on the same horizon as raw ping samples.

Revision ID: 0007_wireless_samples
Revises: 0006_deep_dive_sessions
Create Date: 2026-04-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_wireless_samples"
down_revision: str | None = "0006_deep_dive_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wireless_samples",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("agent_id", sa.Integer, nullable=False),
        sa.Column("agent_interface_id", sa.Integer, nullable=False),
        sa.Column("ts_ms", sa.Integer, nullable=False),
        sa.Column("ssid", sa.String(64), nullable=True),
        sa.Column("bssid", sa.String(17), nullable=True),
        sa.Column("signal_dbm", sa.Integer, nullable=True),
    )
    op.create_index(
        "ix_wireless_samples_agent_ts",
        "wireless_samples",
        ["agent_id", "ts_ms"],
    )
    op.create_index("ix_wireless_samples_ts", "wireless_samples", ["ts_ms"])


def downgrade() -> None:
    op.drop_index("ix_wireless_samples_ts", table_name="wireless_samples")
    op.drop_index("ix_wireless_samples_agent_ts", table_name="wireless_samples")
    op.drop_table("wireless_samples")
