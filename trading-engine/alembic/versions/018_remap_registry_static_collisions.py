"""Deactivate promoted registry rows whose code collides with static A–J."""
from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

_STATIC = ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J")
_REMAP = (("I", "L"), ("J", "M"))


def upgrade() -> None:
    conn = op.get_bind()
    for old_code, new_code in _REMAP:
        row = conn.execute(
            sa.text(
                "SELECT code FROM confluence_registry "
                "WHERE code = :old AND promoted_from IS NOT NULL"
            ),
            {"old": old_code},
        ).first()
        if row is None:
            continue
        taken = conn.execute(
            sa.text("SELECT 1 FROM confluence_registry WHERE code = :new"),
            {"new": new_code},
        ).first()
        if taken is None:
            conn.execute(
                sa.text(
                    "UPDATE confluence_registry SET code = :new WHERE code = :old"
                ),
                {"old": old_code, "new": new_code},
            )
            conn.execute(
                sa.text(
                    "UPDATE confluence_candidates SET proposed_code = :new "
                    "WHERE proposed_code = :old"
                ),
                {"old": old_code, "new": new_code},
            )
        else:
            conn.execute(
                sa.text(
                    "UPDATE confluence_registry SET active = false, "
                    "deactivated_at = NOW() AT TIME ZONE 'UTC' "
                    "WHERE code = :old AND promoted_from IS NOT NULL"
                ),
                {"old": old_code},
            )


def downgrade() -> None:
    pass
