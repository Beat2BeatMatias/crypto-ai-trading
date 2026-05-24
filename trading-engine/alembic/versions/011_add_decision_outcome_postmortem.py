"""Add post-mortem lesson columns to decision_outcomes.

Revision ID: 011
Revises: 010
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None

_POSTMORTEM_CLASSIFICATIONS = (
    "'BAD_BUY','BAD_SELL','MISSED_OPPORTUNITY','BLOCKED_GOOD_TRADE'"
)


def upgrade() -> None:
    op.add_column(
        "decision_outcomes",
        sa.Column("postmortem_status", sa.String(16), nullable=True),
    )
    op.add_column(
        "decision_outcomes",
        sa.Column("lesson_raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "decision_outcomes",
        sa.Column("lesson_normalized", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "decision_outcomes",
        sa.Column("postmortem_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_decision_outcomes_postmortem_pending",
        "decision_outcomes",
        ["computed_at"],
        postgresql_where=sa.text(
            f"postmortem_status IS NULL AND classification IN ({_POSTMORTEM_CLASSIFICATIONS})"
        ),
    )


def downgrade() -> None:
    op.drop_index("idx_decision_outcomes_postmortem_pending", table_name="decision_outcomes")
    op.drop_column("decision_outcomes", "postmortem_at")
    op.drop_column("decision_outcomes", "lesson_normalized")
    op.drop_column("decision_outcomes", "lesson_raw")
    op.drop_column("decision_outcomes", "postmortem_status")
