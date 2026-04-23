"""many-to-one access_point_bssids junction

Each access point can now own N BSSIDs. Ruckus-style vendor variation (radio
index + SSID index jammed into the BSSID) made prefix matching unreliable, so
we switch to explicit mapping: the admin tags each observed BSSID to a known
AP. The first-5-octet matcher in the web UI is dropped.

Revision ID: 0009_access_point_bssids
Revises: 0008_boost_and_percentiles
Create Date: 2026-04-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_access_point_bssids"
down_revision: str | None = "0008_boost_and_percentiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_point_bssids",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "access_point_id", sa.Integer,
            sa.ForeignKey("access_points.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bssid", sa.String(17), nullable=False, unique=True),
        sa.Column("created_at", sa.Integer, nullable=False),
    )
    op.create_index(
        "ix_access_point_bssids_ap", "access_point_bssids", ["access_point_id"]
    )

    # Backfill: preserve the existing single bssid column into the junction.
    bind = op.get_bind()
    now_ms_row = bind.execute(
        sa.text("SELECT CAST(strftime('%s','now') AS INTEGER) * 1000 AS now_ms")
    ).first()
    now_ms = int(now_ms_row.now_ms) if now_ms_row else 0
    for row in bind.execute(
        sa.text("SELECT id, bssid FROM access_points WHERE bssid IS NOT NULL")
    ).all():
        bind.execute(
            sa.text(
                "INSERT INTO access_point_bssids (access_point_id, bssid, created_at)"
                " VALUES (:ap, :b, :ts)"
            ),
            {"ap": row.id, "b": row.bssid, "ts": now_ms},
        )

    with op.batch_alter_table("access_points") as batch:
        batch.drop_constraint("uq_access_points_bssid", type_="unique")
        batch.drop_column("bssid")


def downgrade() -> None:
    with op.batch_alter_table("access_points") as batch:
        batch.add_column(sa.Column("bssid", sa.String(17), nullable=True))

    # Best-effort restore of a single bssid per AP (take the first junction row).
    bind = op.get_bind()
    for ap_id_row in bind.execute(
        sa.text("SELECT DISTINCT access_point_id FROM access_point_bssids")
    ).all():
        first = bind.execute(
            sa.text(
                "SELECT bssid FROM access_point_bssids WHERE access_point_id=:ap"
                " ORDER BY id LIMIT 1"
            ),
            {"ap": ap_id_row.access_point_id},
        ).first()
        if first:
            bind.execute(
                sa.text("UPDATE access_points SET bssid=:b WHERE id=:ap"),
                {"b": first.bssid, "ap": ap_id_row.access_point_id},
            )

    with op.batch_alter_table("access_points") as batch:
        batch.create_unique_constraint("uq_access_points_bssid", ["bssid"])

    op.drop_index(
        "ix_access_point_bssids_ap", table_name="access_point_bssids"
    )
    op.drop_table("access_point_bssids")
