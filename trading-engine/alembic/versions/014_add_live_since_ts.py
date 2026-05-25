"""Add live_since_ts config key and backfill from mode switch history.

Revision ID: 014
Revises: 013
"""
from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None

_ROW = (
    "live_since_ts",
    "",
    "string",
    "ISO UTC timestamp when mode switched to LIVE. Used as default filter cutoff for trades/decisions.",
)


def upgrade() -> None:
    conn = op.get_bind()
    existing = conn.execute(
        sa.text("SELECT 1 FROM config WHERE key = :k"), {"k": _ROW[0]}
    ).fetchone()
    if existing is None:
        conn.execute(
            sa.text(
                "INSERT INTO config (key, value, value_type, description, updated_at) "
                "VALUES (:key, :value, :value_type, :description, NOW())"
            ),
            {
                "key": _ROW[0],
                "value": _ROW[1],
                "value_type": _ROW[2],
                "description": _ROW[3],
            },
        )

    live_switch = conn.execute(
        sa.text(
            "SELECT ts FROM config_history "
            "WHERE key = 'mode' AND new_value = 'LIVE' "
            "ORDER BY ts ASC LIMIT 1"
        )
    ).fetchone()
    if live_switch is None:
        return

    conn.execute(
        sa.text(
            "UPDATE config SET value = :value, updated_at = NOW() "
            "WHERE key = 'live_since_ts' AND COALESCE(value, '') = ''"
        ),
        {"value": live_switch[0].isoformat()},
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM config WHERE key = 'live_since_ts'"))
