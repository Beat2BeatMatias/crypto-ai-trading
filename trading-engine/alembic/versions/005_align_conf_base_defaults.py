"""Align conf_base_* and peso_regime_high_vol defaults with the decisor formula.

conf_base_N now reflects 0.40 + 0.15×N (capped at 1.0).
peso_regime_high_vol corrected from 0.70 → 0.75 to match the prompt.

Revision ID: 005
Revises: 004
Create Date: 2026-05-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

_UPDATES = [
    # (key, new_value, old_value)
    ("conf_base_0",         "0.40", "0.30"),
    ("conf_base_1",         "0.55", "0.50"),
    ("conf_base_2",         "0.70", "0.65"),
    ("conf_base_3",         "0.85", "0.80"),
    ("conf_base_4plus",     "1.00", "0.92"),
    ("peso_regime_high_vol","0.75", "0.70"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for key, new_val, old_val in _UPDATES:
        conn.execute(
            sa.text(
                "UPDATE config SET value = :new, updated_at = NOW() "
                "WHERE key = :key AND value = :old"
            ),
            {"key": key, "new": new_val, "old": old_val},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for key, new_val, old_val in _UPDATES:
        conn.execute(
            sa.text(
                "UPDATE config SET value = :old, updated_at = NOW() "
                "WHERE key = :key AND value = :new"
            ),
            {"key": key, "new": new_val, "old": old_val},
        )
