"""Add min_confluences_short config key for futures SHORT guidance.

Revision ID: 019
Revises: 018
"""
from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None

_ROW = (
    "min_confluences_short",
    "2",
    "int",
    "Guía LLM: mínimo de confluencias bajistas (I/J/F…) para SHORT en futuros. Rango 1–4.",
)


def upgrade() -> None:
    conn = op.get_bind()
    existing = conn.execute(
        sa.text("SELECT 1 FROM config WHERE key = :k"), {"k": _ROW[0]},
    ).fetchone()
    if not existing:
        conn.execute(
            sa.text(
                "INSERT INTO config (key, value, value_type, description) "
                "VALUES (:key, :value, :value_type, :description)"
            ),
            {"key": _ROW[0], "value": _ROW[1], "value_type": _ROW[2], "description": _ROW[3]},
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM config WHERE key = 'min_confluences_short'"))
