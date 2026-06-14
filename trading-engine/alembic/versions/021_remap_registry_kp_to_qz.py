"""Remap promoted registry codes K–P to Q–Z range (static catalog expanded to A–P).

The static confluence catalog was expanded from A–J to A–P, so promoted
entries using codes K–P collide. Remap them to the next available Q–Z letters.
"""
from alembic import op
import sqlalchemy as sa

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None

_STATIC = frozenset("ABCDEFGHIJKLMNOP")
_REMAP = (
    ("K", "R"),
    ("L", "S"),
    ("M", "T"),
    ("N", "U"),
    ("O", "V"),
    ("P", "W"),
)


def upgrade() -> None:
    conn = op.get_bind()
    for old_code, new_code in _REMAP:
        # Check if the old code exists and is a promoted entry (has promoted_from)
        row = conn.execute(
            sa.text(
                "SELECT code FROM confluence_registry "
                "WHERE code = :old AND promoted_from IS NOT NULL"
            ),
            {"old": old_code},
        ).first()
        if row is None:
            continue
        # Check that the new code is not taken
        taken = conn.execute(
            sa.text("SELECT 1 FROM confluence_registry WHERE code = :new AND active IS TRUE"),
            {"new": new_code},
        ).first()
        if taken is None:
            conn.execute(
                sa.text("UPDATE confluence_registry SET code = :new WHERE code = :old"),
                {"old": old_code, "new": new_code},
            )
            conn.execute(
                sa.text("UPDATE confluence_candidates SET proposed_code = :new "
                        "WHERE proposed_code = :old AND status = 'promoted'"),
                {"old": old_code, "new": new_code},
            )
        else:
            # New code taken — deactivate instead
            conn.execute(
                sa.text("UPDATE confluence_registry SET active = false, "
                        "deactivated_at = NOW() AT TIME ZONE 'UTC' "
                        "WHERE code = :old AND promoted_from IS NOT NULL"),
                {"old": old_code},
            )


def downgrade() -> None:
    pass
