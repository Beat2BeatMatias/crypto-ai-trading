"""Add conf_threshold_short_* config keys for futures SHORT guidance.

Revision ID: 020
Revises: 019
"""
from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

_ROWS = [
    (
        "conf_threshold_short_trending_down",
        "0.60",
        "float",
        "Guía LLM: confidence mínima recomendada para SHORT en TRENDING_DOWN (futuros). Rango 0.40–0.85.",
    ),
    (
        "conf_threshold_short_range",
        "0.70",
        "float",
        "Guía LLM: confidence mínima recomendada para SHORT en RANGE (futuros). Rango 0.50–0.90.",
    ),
    (
        "conf_threshold_short_high_vol",
        "0.80",
        "float",
        "Guía LLM: confidence mínima recomendada para SHORT en HIGH_VOLATILITY (futuros). Rango 0.60–0.95.",
    ),
]


def upgrade() -> None:
    conn = op.get_bind()
    for key, value, value_type, description in _ROWS:
        existing = conn.execute(
            sa.text("SELECT 1 FROM config WHERE key = :k"), {"k": key},
        ).fetchone()
        if not existing:
            conn.execute(
                sa.text(
                    "INSERT INTO config (key, value, value_type, description) "
                    "VALUES (:key, :value, :value_type, :description)"
                ),
                {"key": key, "value": value, "value_type": value_type, "description": description},
            )


def downgrade() -> None:
    for key, *_ in _ROWS:
        op.execute(sa.text("DELETE FROM config WHERE key = :k"), {"k": key})
