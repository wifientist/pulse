"""add wireless columns to agent_interfaces + access_points table

Agents report wireless context (SSID/BSSID/signal) for interfaces where
`/sys/class/net/<iface>/wireless` exists. These columns hold the latest values
(null for wired / unassociated interfaces).

`access_points` is a small admin-curated reference table that maps known BSSIDs to
human-readable AP names so the UI can resolve e.g. `aa:bb:cc:dd:ee:ff` → `Attic AP`.

Revision ID: 0005_wireless_and_access_points
Revises: 0004_interface_roles
Create Date: 2026-04-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_wireless_and_access_points"
down_revision: str | None = "0004_interface_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_interfaces") as batch:
        batch.add_column(sa.Column("ssid", sa.String(64), nullable=True))
        batch.add_column(sa.Column("bssid", sa.String(17), nullable=True))
        batch.add_column(sa.Column("signal_dbm", sa.Integer, nullable=True))

    op.create_table(
        "access_points",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("bssid", sa.String(17), nullable=False),
        sa.Column("location", sa.String(128), nullable=True),
        sa.Column("notes", sa.String(512), nullable=True),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
        sa.UniqueConstraint("bssid", name="uq_access_points_bssid"),
    )


def downgrade() -> None:
    op.drop_table("access_points")
    with op.batch_alter_table("agent_interfaces") as batch:
        batch.drop_column("signal_dbm")
        batch.drop_column("bssid")
        batch.drop_column("ssid")
