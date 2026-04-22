"""add Agent.management_ip

Server-observed TCP source IP of the agent's poll connection. Lets operators see which
network interface each agent is using to reach the server — useful when devices have
a management interface (10.0.99.x) separate from their test interface (10.20.x.x).
Populated by the server; agents do not report this value.

Revision ID: 0002_management_ip
Revises: 0001_initial
Create Date: 2026-04-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_management_ip"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("management_ip", sa.String(45), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("management_ip")
