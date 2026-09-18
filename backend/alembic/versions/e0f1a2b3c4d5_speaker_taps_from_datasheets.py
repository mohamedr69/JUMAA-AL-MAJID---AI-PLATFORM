"""speaker tappings taken off the datasheets

The speaker database was first seeded from the tap figures shown on a
design mock-up. Those were wrong for the 70 V line: an EST-S186 taps at
3 / 1.5 / 0.75 / 0.37 W on 70 V, not at the quarter-watt steps a Genesis
speaker uses. This replaces every speaker rule with what its datasheet
says, and withdraws the ones the platform has no datasheet for rather
than leaving invented figures in a database engineers are told to trust.

Rules are versioned, so the old rows are marked superseded rather than
edited: a calculation already issued keeps the values it was made with.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-09-19 05:00:00.000000

"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# What each datasheet in the library actually says. `taps` is the 70 V
# line, which is what these systems run on; the 100 V figures are kept
# beside them because the same speaker is used both ways and an engineer
# checking the rule should see what was read.
#
# No `default_tap`: a datasheet lists what a speaker *can* be set to, not
# what this company sets it to. Guessing one is what put the wrong figures
# here in the first place, so the engineer chooses and the page asks until
# they have.
FROM_DATASHEETS: list[dict] = [
    {
        "part_no": "EST-S186C",
        "description": "EST-S186 ceiling loudspeaker, ABS fire dome, rated 6 W",
        "line_volts": 70,
        "taps": [0.37, 0.75, 1.5, 3],
        "taps_100v": [0.75, 1.5, 3, 6],
        "rated_watts": 6,
        "datasheet": {"library": "EDWARDS", "path": "03- NAC/Speaker/03- EST-S186.pdf", "pages": [2]},
        "source": (
            "EST-S186 datasheet, page 2: \"Tappings 70 V line, W -- 3 / 1,5 / 0,75 / 0,37\" "
            "(100 V line: 6 / 3 / 1,5 / 0,75; rated power 6 W)."
        ),
    },
    {
        "part_no": "G4SRN",
        "description": "Genesis G4 wall loudspeaker, selectable 25 V / 70 V",
        "line_volts": 70,
        "taps": [0.25, 0.5, 1, 2],
        "datasheet": {"library": "EDWARDS", "path": "03- NAC/Speaker/01- G4 WM.pdf", "pages": [4]},
        "source": (
            "Genesis G4 datasheet, page 4 sound settings table: taps of 1/4 W, 1/2 W, 1 W and 2 W, "
            "at 25 V or 70 V by a switch under the cover."
        ),
    },
    {
        "part_no": "757-3A-SS70",
        "description": "Integrity 70 V speaker/strobe",
        "line_volts": 70,
        "taps": [0.25, 0.5, 1, 2],
        "datasheet": {"library": "EDWARDS", "path": "03- NAC/Speaker/01- 757-3A-SS70 WP + 757WP WM.pdf",
                      "pages": [1]},
        "source": (
            "Integrity 757 datasheet, page 1: \"Multiple Output Taps, 25 or 70 Volt Models -- easy to "
            "select for 1/4, 1/2, 1 or 2 watt operation.\""
        ),
    },
]

# Seeded from a mock-up, with no datasheet behind them. Withdrawn: a
# speaker the platform cannot vouch for is better shown as having no
# tapping -- which the page asks about -- than as having a made-up one.
WITHDRAWN = ["EST-S1830", "EST-S1814", "EST-SPK-15", "EST-SPK-30", "EST-SPK-50"]


def upgrade() -> None:
    """Upgrade schema."""
    rules = sa.table(
        "design_rules",
        sa.column("id", sa.Integer), sa.column("category", sa.String), sa.column("key", sa.String),
        sa.column("version", sa.Integer), sa.column("data", sa.JSON), sa.column("source", sa.Text),
        sa.column("created_at", sa.DateTime), sa.column("superseded_at", sa.DateTime),
    )
    connection = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    existing = connection.execute(
        sa.select(rules.c.key, sa.func.max(rules.c.version))
        .where(rules.c.category == "ve.speaker")
        .group_by(rules.c.key)
    ).all()
    versions = {key: version for key, version in existing}

    # Everything that was there came from the mock-up.
    connection.execute(
        sa.update(rules)
        .where(rules.c.category == "ve.speaker", rules.c.superseded_at.is_(None))
        .values(superseded_at=now)
    )

    for speaker in FROM_DATASHEETS:
        data = {key: value for key, value in speaker.items() if key != "source"}
        connection.execute(rules.insert().values(
            category="ve.speaker", key=speaker["part_no"],
            version=versions.get(speaker["part_no"], 0) + 1,
            # The column is JSON; SQLAlchemy serialises it. Passing a
            # string here would store a JSON string of a JSON string.
            data=data,
            source=speaker["source"], created_at=now, superseded_at=None,
        ))
    # The withdrawn ones keep their superseded rows and gain no new one.


def downgrade() -> None:
    """Downgrade schema.

    The datasheet-backed rows are withdrawn and whatever was in force
    before this ran is put back.
    """
    rules = sa.table(
        "design_rules",
        sa.column("id", sa.Integer), sa.column("category", sa.String), sa.column("key", sa.String),
        sa.column("version", sa.Integer), sa.column("source", sa.Text),
        sa.column("superseded_at", sa.DateTime),
    )
    connection = op.get_bind()
    # Only the rows this migration added: the newest version of each part
    # it wrote. Matching on the word "datasheet" in the source would also
    # catch the rows it replaced, which is how this was got wrong once.
    for speaker in FROM_DATASHEETS:
        newest = connection.execute(
            sa.select(sa.func.max(rules.c.version))
            .where(rules.c.category == "ve.speaker", rules.c.key == speaker["part_no"])
        ).scalar()
        if newest is not None:
            connection.execute(sa.delete(rules).where(
                rules.c.category == "ve.speaker",
                rules.c.key == speaker["part_no"],
                rules.c.version == newest,
            ))
    connection.execute(
        sa.update(rules).where(rules.c.category == "ve.speaker").values(superseded_at=None)
    )
