"""replace is_primary_test with role enum

Admins classify each agent interface: `test` (peers ping its IP), `management`
(informational — mgmt plane only), `ignored` (don't care about it), `unknown` (freshly
reported, awaiting classification). Backfills existing `is_primary_test=1` rows to
role='test' and everything else to role='unknown'.

Revision ID: 0004_interface_roles
Revises: 0003_agent_interfaces
Create Date: 2026-04-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_interface_roles"
down_revision: str | None = "0003_agent_interfaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_interfaces") as batch:
        batch.add_column(
            sa.Column("role", sa.String(16), nullable=False, server_default="unknown")
        )
    # Backfill any existing primary_test flag into the new role column.
    op.execute(
        "UPDATE agent_interfaces SET role='test' WHERE is_primary_test=1"
    )
    with op.batch_alter_table("agent_interfaces") as batch:
        batch.drop_column("is_primary_test")


def downgrade() -> None:
    with op.batch_alter_table("agent_interfaces") as batch:
        batch.add_column(
            sa.Column("is_primary_test", sa.Boolean, nullable=False, server_default="0")
        )
    op.execute(
        "UPDATE agent_interfaces SET is_primary_test=1 WHERE role='test'"
    )
    with op.batch_alter_table("agent_interfaces") as batch:
        batch.drop_column("role")
