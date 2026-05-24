"""Add confluence_candidates and confluence_registry tables.

Revision ID: 012
Revises: 011
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "confluence_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("pattern_tag", sa.String(64), nullable=False),
        sa.Column("proposed_code", sa.String(1), nullable=True),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("definition_md", sa.Text(), nullable=False),
        sa.Column("verify_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_decision_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pattern_tag"),
    )
    op.create_index(
        "idx_confluence_candidates_status",
        "confluence_candidates",
        ["status", "occurrence_count"],
        postgresql_ops={"occurrence_count": "DESC"},
    )

    op.create_table(
        "confluence_registry",
        sa.Column("code", sa.String(1), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("definition_md", sa.Text(), nullable=False),
        sa.Column("verify_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("promoted_from", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["promoted_from"], ["confluence_candidates.id"]),
        sa.PrimaryKeyConstraint("code"),
        sa.UniqueConstraint("slug"),
    )


def downgrade() -> None:
    op.drop_table("confluence_registry")
    op.drop_index("idx_confluence_candidates_status", table_name="confluence_candidates")
    op.drop_table("confluence_candidates")
