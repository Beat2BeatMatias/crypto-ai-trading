"""Add GIN indexes on JSONB columns, partial index on playbook active, and missing FK.

Indexes declared in the ORM but absent from migrations:
  - idx_indicators_data  : GIN on indicators.data
  - idx_decisions_input  : GIN on decisions.input
  - idx_decisions_output : GIN on decisions.output
  - idx_playbook_active  : partial UNIQUE on playbook_versions.active WHERE active = true

Missing FK (circular ref handled with use_alter):
  - trades.decision_id -> decisions.id

Revision ID: 006
Revises: 005
Create Date: 2026-05-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_indicators_data", "indicators", ["data"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_decisions_input", "decisions", ["input"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_decisions_output", "decisions", ["output"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_playbook_active", "playbook_versions", ["active"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )
    op.create_foreign_key(
        "fk_trades_decision_id",
        "trades", "decisions",
        ["decision_id"], ["id"],
        use_alter=True,
    )


def downgrade() -> None:
    op.drop_constraint("fk_trades_decision_id", "trades", type_="foreignkey")
    op.drop_index("idx_playbook_active", table_name="playbook_versions")
    op.drop_index("idx_decisions_output", table_name="decisions")
    op.drop_index("idx_decisions_input", table_name="decisions")
    op.drop_index("idx_indicators_data", table_name="indicators")
