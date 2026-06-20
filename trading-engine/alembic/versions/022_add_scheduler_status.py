"""Add scheduler_status table for tracking scheduled processes.

Revision ID: 022
Revises: 021
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None

_INITIAL_ROWS = [
    ("decisor", "Decisor", "~5 min"),
    ("supervisor", "Supervisor", "cada 6h"),
    ("outcome_attribution", "Outcome", "~60 min"),
    ("fees", "Fees", "24 h"),
]


def upgrade() -> None:
    op.create_table(
        "scheduler_status",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("next_run_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_run_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("interval_desc", sa.String(64), nullable=True),
    )

    conn = op.get_bind()
    for job_id, name, interval_desc in _INITIAL_ROWS:
        conn.execute(
            sa.text(
                "INSERT INTO scheduler_status (job_id, name, interval_desc) "
                "VALUES (:job_id, :name, :interval_desc)"
                " ON CONFLICT (job_id) DO NOTHING"
            ),
            {"job_id": job_id, "name": name, "interval_desc": interval_desc},
        )


def downgrade() -> None:
    op.drop_table("scheduler_status")
