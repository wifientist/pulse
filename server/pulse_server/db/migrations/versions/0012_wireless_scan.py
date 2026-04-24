"""monitored SSIDs allowlist + wireless scan samples

Adds support for a monitor-role agent that runs `iw dev <iface> scan` and
reports visible BSSIDs. `monitored_ssids` is the admin-curated allowlist the
server filters incoming scans against. `wireless_scan_samples` stores every
(agent, bssid, ts) datapoint that passes the filter — the Trends page reads
these to render per-BSSID signal over time.

Revision ID: 0012_wireless_scan
Revises: 0011_tools_and_attenuator
Create Date: 2026-04-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_wireless_scan"
down_revision: str | None = "0011_tools_and_attenuator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitored_ssids",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ssid", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.Integer, nullable=False),
    )

    op.create_table(
        "wireless_scan_samples",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("agent_id", sa.Integer, nullable=False),
        sa.Column("ts_ms", sa.Integer, nullable=False),
        sa.Column("bssid", sa.String(17), nullable=False),
        sa.Column("ssid", sa.String(64), nullable=True),
        sa.Column("signal_dbm", sa.Integer, nullable=True),
        sa.Column("frequency_mhz", sa.Integer, nullable=True),
        sa.Column("channel_width_mhz", sa.Integer, nullable=True),
    )
    op.create_index(
        "ix_scan_samples_bssid_ts",
        "wireless_scan_samples",
        ["bssid", "ts_ms"],
    )
    op.create_index(
        "ix_scan_samples_agent_ts",
        "wireless_scan_samples",
        ["agent_id", "ts_ms"],
    )
    op.create_index("ix_scan_samples_ts", "wireless_scan_samples", ["ts_ms"])


def downgrade() -> None:
    op.drop_index("ix_scan_samples_ts", table_name="wireless_scan_samples")
    op.drop_index("ix_scan_samples_agent_ts", table_name="wireless_scan_samples")
    op.drop_index("ix_scan_samples_bssid_ts", table_name="wireless_scan_samples")
    op.drop_table("wireless_scan_samples")
    op.drop_table("monitored_ssids")
