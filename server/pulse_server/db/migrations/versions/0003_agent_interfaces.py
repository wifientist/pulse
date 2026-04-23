"""add agent_interfaces table

Tracks per-agent network interfaces by MAC. MAC is the stable identifier across DHCP
changes; `current_ip` updates as the interface's IP changes over time. `is_primary_test`
flags the one interface whose IP gets snapshotted into peer_assignments.target_ip for
inbound pings from other agents.

Revision ID: 0003_agent_interfaces
Revises: 0002_management_ip
Create Date: 2026-04-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_agent_interfaces"
down_revision: str | None = "0002_management_ip"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_interfaces",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("agent_id", sa.Integer, nullable=False),
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("current_ip", sa.String(45)),
        sa.Column("iface_name", sa.String(64)),
        sa.Column("is_primary_test", sa.Boolean, nullable=False),
        sa.Column("first_seen", sa.Integer, nullable=False),
        sa.Column("last_seen", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.UniqueConstraint("agent_id", "mac", name="uq_agent_interfaces_agent_mac"),
    )
    op.create_index("ix_agent_interfaces_agent_id", "agent_interfaces", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_interfaces_agent_id", table_name="agent_interfaces")
    op.drop_table("agent_interfaces")
