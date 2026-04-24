"""tools framework + attenuator preset/run tables + ruckus_serial on AP

Generic `tool_runs` / `tool_run_steps` so future tools share the state
machine. `attenuator_presets` stores reusable per-AP ramp configs.

`access_points` gets a `ruckus_serial` column so Pulse's curated AP list
can be tied to the upstream Ruckus One AP identity.

Revision ID: 0011_tools_and_attenuator
Revises: 0010_passive_targets
Create Date: 2026-04-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_tools_and_attenuator"
down_revision: str | None = "0010_passive_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("access_points") as batch:
        batch.add_column(sa.Column("ruckus_serial", sa.String(32), nullable=True))
    op.create_index(
        "ix_access_points_ruckus_serial", "access_points", ["ruckus_serial"]
    )

    op.create_table(
        "attenuator_presets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("radio", sa.String(8), nullable=False),  # "5g" | "24g" | "6g"
        sa.Column("step_size_db", sa.Integer, nullable=False),
        sa.Column("step_interval_s", sa.Integer, nullable=False),
        # participants: [{ ap_id, direction: "drop"|"raise", target_value: "MAX"|"-1".."-10"|"MIN" }]
        sa.Column("participants", sa.JSON, nullable=False),
        sa.Column("boost_participants", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
    )

    op.create_table(
        "tool_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tool_type", sa.String(32), nullable=False),  # "attenuator" for now
        sa.Column("preset_id", sa.Integer, nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        # pending | running | completed | failed | cancelled
        sa.Column("config", sa.JSON, nullable=False),
        # revert_state: [{ ap_id, radio, tx_power }] captured at run start
        sa.Column("revert_state", sa.JSON, nullable=True),
        sa.Column("started_at", sa.Integer, nullable=False),
        sa.Column("ends_at", sa.Integer, nullable=False),
        sa.Column("finalized_at", sa.Integer, nullable=True),
        sa.Column("error", sa.String(1024), nullable=True),
    )
    op.create_index("ix_tool_runs_state", "tool_runs", ["state"])

    op.create_table(
        "tool_run_steps",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, nullable=False),
        sa.Column("ts_ms", sa.Integer, nullable=False),
        sa.Column("ap_serial", sa.String(32), nullable=True),
        sa.Column("action", sa.JSON, nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("ruckus_request_id", sa.String(64), nullable=True),
        sa.Column("error", sa.String(1024), nullable=True),
    )
    op.create_index("ix_tool_run_steps_run", "tool_run_steps", ["run_id", "ts_ms"])


def downgrade() -> None:
    op.drop_index("ix_tool_run_steps_run", table_name="tool_run_steps")
    op.drop_table("tool_run_steps")
    op.drop_index("ix_tool_runs_state", table_name="tool_runs")
    op.drop_table("tool_runs")
    op.drop_table("attenuator_presets")
    op.drop_index("ix_access_points_ruckus_serial", table_name="access_points")
    with op.batch_alter_table("access_points") as batch:
        batch.drop_column("ruckus_serial")
