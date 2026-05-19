"""Add decision_outcomes table for counterfactual attribution.

Stores forward returns (MFE/MAE) and classification per decisor decision,
populated by outcome_attribution_job. 1-to-1 with decisions via PK FK.

Revision ID: 008
Revises: 007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_outcomes",
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decisions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("horizon_min", sa.Integer, nullable=False),
        sa.Column("matured", sa.Boolean, nullable=False),
        sa.Column("forward_return_pct", sa.Numeric(10, 5), nullable=True),
        sa.Column("mfe_pct", sa.Numeric(10, 5), nullable=True),
        sa.Column("mae_pct", sa.Numeric(10, 5), nullable=True),
        sa.Column("time_to_mfe_min", sa.Integer, nullable=True),
        sa.Column("time_to_mae_min", sa.Integer, nullable=True),
        sa.Column("sl_dist_pct", sa.Numeric(10, 5), nullable=True),
        sa.Column("tp_target_pct", sa.Numeric(10, 5), nullable=True),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_decision_outcomes_classification",
        "decision_outcomes",
        ["classification", "computed_at"],
    )
    op.create_index(
        "idx_decision_outcomes_pending",
        "decision_outcomes",
        ["computed_at"],
        postgresql_where=sa.text("classification = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("idx_decision_outcomes_pending", table_name="decision_outcomes")
    op.drop_index("idx_decision_outcomes_classification", table_name="decision_outcomes")
    op.drop_table("decision_outcomes")
