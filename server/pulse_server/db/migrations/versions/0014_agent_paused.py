"""agents: paused flag for soft-stop without revocation

When set, the poll handler hands the agent an empty peer list, no commands,
and no scan_ifaces — pings stop within one poll cycle. The agent itself
keeps polling so it's instantly resumable. The alert engine also skips any
pair where either endpoint is paused, so resume doesn't trigger a flurry
of recovery alerts.

Revision ID: 0014_agent_paused
Revises: 0013_attenuator_instant
Create Date: 2026-04-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_agent_paused"
down_revision: str | None = "0013_attenuator_instant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column(
                "paused",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("paused")
