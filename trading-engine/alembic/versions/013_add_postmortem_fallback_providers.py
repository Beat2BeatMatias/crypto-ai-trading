"""Add postmortem_fallback_providers config key.

Revision ID: 013
Revises: 012
"""
from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None

_ROW = (
    "postmortem_fallback_providers",
    "groq-compound-mini,groq-llama-4-scout,groq-qwen3-32b,groq-gpt-oss-20b,groq-llama-3.1-8b",
    "string",
    "Cascada de fallback para post-mortem (CSV ordenado). Mismas opciones que fallback_providers.",
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
        sa.text("DELETE FROM config WHERE key = 'postmortem_fallback_providers'")
    )
