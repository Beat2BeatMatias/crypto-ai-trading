"""seed decisor_v2 config entries

Revision ID: 004
Revises: 003
Create Date: 2026-05-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

_NEW_ROWS = [
    ("min_fees_to_tp_ratio", "3.0", "float",
     "Min TP movement as multiple of round-trip fees for BUY approval (R10). Range 1.5–6.0."),
    ("min_confluences_buy", "2", "int",
     "Minimum number of confluences required to allow BUY. Range 1–4."),
    ("cooldown_after_sell_min", "15", "int",
     "Minutes of cooldown after a SELL before next BUY is allowed. Range 0–120."),
    ("subjective_adj_max", "0.10", "float",
     "Maximum allowed subjective confidence adjustment (±). Range 0.00–0.20."),
    ("expected_holding_max_min", "240", "int",
     "Maximum expected holding time in minutes; used for zombie-trade detection. Range 30–1440."),
    ("confluence_weak_factor", "0.5", "float",
     "Multiplier applied to a weak confluence vs a solid one in confidence calc. Range 0.0–1.0."),
]


def upgrade() -> None:
    conn = op.get_bind()
    for key, value, value_type, description in _NEW_ROWS:
        existing = conn.execute(
            sa.text("SELECT 1 FROM config WHERE key = :k"), {"k": key}
        ).fetchone()
        if existing is None:
            conn.execute(
                sa.text(
                    "INSERT INTO config (key, value, value_type, description, updated_at) "
                    "VALUES (:key, :value, :value_type, :description, NOW())"
                ),
                {"key": key, "value": value, "value_type": value_type, "description": description},
            )


def downgrade() -> None:
    conn = op.get_bind()
    for key, *_ in _NEW_ROWS:
        conn.execute(sa.text("DELETE FROM config WHERE key = :k"), {"k": key})
