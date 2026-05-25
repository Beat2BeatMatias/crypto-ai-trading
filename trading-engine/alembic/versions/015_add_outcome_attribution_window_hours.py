"""Add outcome_attribution_window_hours config key.

Revision ID: 015
Revises: 014
"""
from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

_ROW = (
    "outcome_attribution_window_hours",
    "25",
    "int",
    "Ventana compartida (horas) para outcome attribution y post-mortem. Rango 12–72.",
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


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM config WHERE key = 'outcome_attribution_window_hours'")
    )
