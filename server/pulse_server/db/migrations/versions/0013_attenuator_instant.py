"""attenuator: add `instant` flag for one-shot (no-ramp) runs

When set, `start_run` jumps each participant directly to its target txPower
in a single Ruckus call and does not restore on completion — semantically
"apply permanently" vs. the default transient-ramp-with-restore.

Revision ID: 0013_attenuator_instant
Revises: 0012_wireless_scan
Create Date: 2026-04-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_attenuator_instant"
down_revision: str | None = "0012_wireless_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("attenuator_presets") as batch:
        batch.add_column(
            sa.Column(
                "instant",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("attenuator_presets") as batch:
        batch.drop_column("instant")
