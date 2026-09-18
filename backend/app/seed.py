from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import DesignRule, RoleEnum, User

settings = get_settings()

VE_LOAD_LIMIT_CATEGORY = "ve.limit"
VE_LOAD_LIMIT_KEY = "amplifier_max_load"
BATTERY_SIZING_CATEGORY = "battery.sizing"
BATTERY_SIZING_KEY = "fas_panel"
BATTERY_SELECTION_CATEGORY = "battery.selection"
BATTERY_SELECTION_KEY = "fas_panel"
# Catalogue categories, keyed by part number and entered from datasheets --
# never seeded.
PART_CURRENT_CATEGORY = "part.current"
BATTERY_UNIT_CATEGORY = "battery.unit"

# Rules the platform starts with. Each is a decision someone made, not a
# default picked in code, and `source` says whose -- a rule without one has
# no business deciding pass or fail. Seeded only when the rule has no row at
# all, so a correction made in the database (a new version) is never undone
# by a restart.
INITIAL_DESIGN_RULES = [
    {
        "category": VE_LOAD_LIMIT_CATEGORY,
        "key": VE_LOAD_LIMIT_KEY,
        "data": {"fraction": 0.8},
        "source": (
            "Decided by the platform owner on 2026-09-11: a channel fails when its required "
            "watts exceed 80% of its amplifier's rating (20% spare), e.g. 40 W on a 50 W "
            "amplifier. The engineers' amplifier workbooks apply no limit of their own."
        ),
    },
    {
        "category": BATTERY_SIZING_CATEGORY,
        "key": BATTERY_SIZING_KEY,
        "data": {"standby_hours": 24, "alarm_minutes": 30, "spare_factor": 1.2, "panel_voltage": 24},
        "source": (
            "The engineers' FAS battery workbooks (EP-20779 '6. FAS Battery Calculation.xlsx', "
            "EP-30784 'BC.xlsx'): required Ah = (standby mA x 24 h + alarm mA x 30 min) / 1000 x 1.2 "
            "(20% spare); confirmed by the platform owner on 2026-09-11. Panels run on 24 V DC: the "
            "BOQs quote their batteries as pairs of 12 V blocks (e.g. EP-30784's 2 x 12V65A)."
        ),
    },
    {
        "category": BATTERY_SELECTION_CATEGORY,
        "key": BATTERY_SELECTION_KEY,
        "data": {"brand": "ROCKET"},
        "source": (
            "Decided by the platform owner on 2026-09-11: panel batteries are selected from the ROCKET "
            "range -- the nearest size that covers the requirement -- using the ROCKET datasheets in "
            "the datasheet library."
        ),
    },
    # --- the 24 V power calculation --------------------------------------
    #
    # The booster power supply the floors are fed from, and how hard it is
    # worked. Rules rather than code, so either is corrected without a
    # release.
    {
        "category": "power.supply",
        "key": "booster",
        "data": {"part_no": "BPS10A/230", "description": "10 Amp Booster Power Supply, 220V",
                 "amps": 10, "fraction": 0.8},
        "source": (
            "Equipment current table: BPS10A/230, 10 A booster power supply. Loaded to the same "
            "fraction of its rating as an amplifier."
        ),
    },
    {
        "category": "power.module",
        "key": "nac",
        "data": {"part_no": "SIGA-CC1", "description": "Single Input (Riser) Module", "per_floor": 1},
        "source": (
            "Decided by the platform owner: a floor's notification circuit is driven from one "
            "SIGA-CC1. A sounder base is on the Signature loop and needs none."
        ),
    },
    # What each 24 V appliance draws, read off its datasheet in the
    # library. Where a datasheet gives more than one figure -- a strobe by
    # candela, a horn by volume -- every one is offered and the engineer
    # chooses; nothing is picked for them.
    {
        "category": "power.device",
        "key": "G1ARN",
        "data": {
            "part_no": "G1ARN",
            "description": "Genesis G1 compact wall horn",
            "currents": [
                {"ma": 13, "label": "C-LOW / T-LOW, 16-33 VDC"},
                {"ma": 23, "label": "C-HIGH / T-HIGH, 16-33 VDC"},
                {"ma": 15, "label": "C-LOW / T-LOW, 16-33 VFWR"},
                {"ma": 29, "label": "C-HIGH / T-HIGH, 16-33 VFWR"},
            ],
            "datasheet": {"library": "EDWARDS", "path": "03- NAC/Horn-Strobe/02- G1 series WM.pdf",
                          "pages": [3]},
        },
        "source": (
            "Genesis G1 series datasheet, page 3, operating current: horns draw 13 mA on C-LOW or "
            "T-LOW and 23 mA on C-HIGH or T-HIGH at 16 to 33 VDC (15 and 29 mA on VFWR). Factory "
            "set to high dB."
        ),
    },
    {
        "category": "power.device",
        "key": "202-7A-T",
        "data": {
            "part_no": "202-7A-T",
            "description": "Xenon flasher",
            "currents": [
                {"ma": 90, "label": "15/75 cd, 24 Vdc"},
                {"ma": 180, "label": "110 cd, 24 Vdc"},
                {"ma": 128, "label": "15/75 cd, 24 Vfwr"},
                {"ma": 260, "label": "110 cd, 24 Vfwr"},
            ],
            "datasheet": {"library": "EDWARDS",
                          "path": "03- NAC/Horn-Strobe/202-7A-T Flasher Emar approved.pdf", "pages": [3]},
        },
        "source": (
            "202 series datasheet, page 3, typical current: 90 mA at 15/75 cd and 180 mA at 110 cd "
            "on 24 Vdc (128 and 260 mA on 24 Vfwr)."
        ),
    },
    {
        "category": "power.device",
        "key": "SIGA-LPS",
        "data": {
            "part_no": "SIGA-LPS",
            "description": "Low profile sounder base",
            # The base is the half of "Smoke with Sounder Base" that draws
            # the current, whichever detector sits in it. Factory set to
            # high dBA, and a booster's output is 24 Vdc filtered and
            # regulated, so the datasheet itself settles the figure.
            "currents": [
                {"ma": 24, "label": "Low dBA, 24 VDC"},
                {"ma": 41, "label": "High dBA, 24 VDC", "default": True},
                {"ma": 51, "label": "Low dBA, 24 VFWR"},
                {"ma": 60, "label": "High dBA, 24 VFWR"},
            ],
            "datasheet": {"library": "EDWARDS",
                          "path": "02- Initiating Devices/06- bases/low profile/04- SIGA_LPS.pdf", "pages": [4]},
        },
        "source": (
            "SIGA-LPS datasheet, page 4, Operating Current (RMS): 24 mA at low dBA and 41 mA at high dBA "
            "on 24 VDC (51 and 60 mA on 24 VFWR). Default output volume: high dBA. Supervisory current "
            "1.46 mA DC."
        ),
    },
    {
        "category": "power.device",
        "key": "757-7A-T",
        "data": {
            "part_no": "757-7A-T",
            "description": "Integrity temporal horn-strobe, 15/75 cd, weatherproof",
            # A horn-strobe draws for both halves: the 15/75 cd strobe, and
            # the horn at whichever output it is set to. The datasheet names
            # no factory setting for the horn, so both are offered.
            "currents": [
                {"ma": 110, "label": "Horn low + 15/75 cd strobe, 24 VDC"},
                {"ma": 130, "label": "Horn high + 15/75 cd strobe, 24 VDC"},
                {"ma": 156, "label": "Horn low + 15/75 cd strobe, 24 VFWR"},
                {"ma": 183, "label": "Horn high + 15/75 cd strobe, 24 VFWR"},
            ],
            "datasheet": {"library": "EDWARDS",
                          "path": "03- NAC/Horn-Strobe/01- 757-7A-T WP + 757-WB WM.pdf", "pages": [3, 4]},
        },
        "source": (
            "757 series datasheet. Page 3, strobe operating current (RMS) at 15/75 cd: 90 mA on 24 Vdc, "
            "128 mA on 24 Vfwr. Page 4, horn current: 20 mA low and 40 mA high on 24 Vdc (28 and 55 mA "
            "on 24 Vrms FWR). Each figure offered is the strobe and the horn together."
        ),
    },
    {
        "category": "power.device",
        "key": "G1RF",
        "data": {
            "part_no": "G1RF",
            "description": "Genesis G1 LED strobe",
            "currents": [
                {"ma": 24, "label": "15/30/75 cd, 16-33 VDC"},
                {"ma": 32, "label": "15/30/75 cd, 16-33 VFWR"},
            ],
            "datasheet": {"library": "EDWARDS", "path": "03- NAC/Horn-Strobe/02- G1 series WM.pdf",
                          "pages": [3]},
        },
        "source": (
            "Genesis G1 series datasheet, page 3: an LED strobe draws 24 mA at any of its 15, 30 or "
            "75 cd settings on 16 to 33 VDC."
        ),
    },
    {
        "category": "power.device",
        "key": "G1RHDVM",
        "data": {
            "part_no": "G1RHDVM",
            "description": "Genesis G1 horn-strobe",
            "currents": [
                {"ma": 35, "label": "C-Low / T-Low, 16-33 VDC"},
                {"ma": 45, "label": "C-High / T-High, 16-33 VDC"},
                {"ma": 43, "label": "C-Low / T-Low, 16-33 VFWR"},
                {"ma": 55, "label": "C-High / T-High, 16-33 VFWR"},
            ],
            "datasheet": {"library": "EDWARDS", "path": "03- NAC/Horn-Strobe/02- G1 series WM.pdf",
                          "pages": [3]},
        },
        "source": (
            "Genesis G1 series datasheet, page 3: a horn-strobe at any candela setting draws 35 mA "
            "on C-Low or T-Low and 45 mA on C-High or T-High at 16 to 33 VDC."
        ),
    },
    # The audio riser module each floor is fed through, and what one may
    # carry. A rule rather than code, so the part or the limit is corrected
    # without a release.
    {
        "category": "ve.module",
        "key": "audio_riser",
        "data": {
            "part_no": "SIGA-CC2A",
            "description": "Dual Input (Riser) Module - Class A",
            "max_watts": 35,
            "per_floor": 1,
        },
        "source": (
            "Decided by the platform owner: every floor is fed through one SIGA-CC2A, and one "
            "module carries at most 35 W of speaker load."
        ),
    },
    # The speaker database the amplifier calculation loads from: what each
    # speaker can be tapped at, taken off its datasheet in the library.
    # A rule rather than code, so a tapping is corrected without a release,
    # and versioned, so a calculation already issued does not shift when
    # one is.
    #
    # `taps` is the **70 V line**, which is what these systems run on. The
    # same speaker taps differently on 100 V and the figures are not
    # interchangeable: an EST-S186 is 3 / 1.5 / 0.75 / 0.37 W on 70 V and
    # 6 / 3 / 1.5 / 0.75 W on 100 V.
    #
    # There is deliberately no default tapping. A datasheet says what a
    # speaker *can* be set to, not what this company sets it to; the
    # engineer chooses, and the amplifier page asks until they have.
    {
        "category": "ve.speaker",
        "key": "EST-S186C",
        "data": {
            "part_no": "EST-S186C",
            "description": "EST-S186 ceiling loudspeaker, ABS fire dome, rated 6 W",
            "line_volts": 70,
            "taps": [0.37, 0.75, 1.5, 3],
            "taps_100v": [0.75, 1.5, 3, 6],
            "rated_watts": 6,
            "datasheet": {"library": "EDWARDS", "path": "03- NAC/Speaker/03- EST-S186.pdf", "pages": [2]},
        },
        "source": (
            "EST-S186 datasheet, page 2: \"Tappings 70 V line, W -- 3 / 1,5 / 0,75 / 0,37\" "
            "(100 V line: 6 / 3 / 1,5 / 0,75; rated power 6 W)."
        ),
    },
    {
        "category": "ve.speaker",
        "key": "G4SRN",
        "data": {
            "part_no": "G4SRN",
            "description": "Genesis G4 wall loudspeaker, selectable 25 V / 70 V",
            "line_volts": 70,
            "taps": [0.25, 0.5, 1, 2],
            "datasheet": {"library": "EDWARDS", "path": "03- NAC/Speaker/01- G4 WM.pdf", "pages": [4]},
        },
        "source": (
            "Genesis G4 datasheet, page 4 sound settings table: taps of 1/4 W, 1/2 W, 1 W and 2 W, "
            "at 25 V or 70 V by a switch under the cover."
        ),
    },
    {
        "category": "ve.speaker",
        "key": "757-3A-SS70",
        "data": {
            "part_no": "757-3A-SS70",
            "description": "Integrity 70 V speaker/strobe",
            "line_volts": 70,
            "taps": [0.25, 0.5, 1, 2],
            "datasheet": {"library": "EDWARDS",
                          "path": "03- NAC/Speaker/01- 757-3A-SS70 WP + 757WP WM.pdf", "pages": [1]},
        },
        "source": (
            "Integrity 757 datasheet, page 1: \"Multiple Output Taps, 25 or 70 Volt Models -- easy to "
            "select for 1/4, 1/2, 1 or 2 watt operation.\""
        ),
    },
]


def seed_design_rules(db: Session) -> None:
    for rule in INITIAL_DESIGN_RULES:
        exists = (
            db.query(DesignRule)
            .filter(DesignRule.category == rule["category"], DesignRule.key == rule["key"])
            .first()
        )
        if not exists:
            db.add(DesignRule(version=1, **rule))
    db.commit()


def seed_default_admin(db: Session) -> None:
    existing = db.query(User).filter(User.email == settings.default_admin_email).first()
    if existing:
        return

    admin = User(
        email=settings.default_admin_email,
        full_name="Platform Administrator",
        hashed_password=hash_password(settings.default_admin_password),
        role=RoleEnum.admin,
    )
    db.add(admin)
    db.commit()
