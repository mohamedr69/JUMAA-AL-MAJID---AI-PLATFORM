"""Which of a project's proposed materials a floor-wise line may be
settled as.

The list is mostly not field devices. One real project's fire alarm
materials run to forty parts, of which the panel's own processor,
firewall, amplifier and door assembly, three kinds of box, two detector
bases and a gasket are none of them installed on a floor. Offering them
would let a floor's smoke detectors be settled as a back box.

The part numbers and wordings here are EP-30880's and EP-30784's.
"""

from app.services.schedule_materials import is_device, is_sounder_base

# (part number, description) -> whether it is a field device.
REAL_MATERIALS: list[tuple[str, str, bool]] = [
    # The field devices, which are what the schedule counts.
    ("SIGA-OSD-FCN", "Intelligent Photoelectric Smoke Detector", True),
    ("SIGA-OSHD-FCN", "Intelligent 3D Multisensor Detector - Photoelectric, Heat", True),
    ("SIGA-HRD-FCN", "Intelligent Fixed Temperature / Rate-of-Rise Heat Detector", True),
    ("SIGA-SD", "SuperDuct", True),
    ("SIGA-278", "Manual Pull Station - Double Action, 1-stage", True),
    ("G4SRN", "Wall Speaker, Red, No Marking", True),
    ("EST-S186C", "Ceiling speaker, ABS fire dome (diameter 17,5 cm)", True),
    ("G1ARN", "Compact Wall Horn, Red, No Marking", True),
    ("757-3A-SS70", "30cd Speaker/Strobe - 70V, RED.", True),
    ("6833-4", "Four-state Portable Telephone Handset Receptacle", True),   # the socket on the wall
    ("SIGA-CT2", "Dual Input Module", True),
    ("SIGA-CR", "Control Relay Module", True),
    ("SIGA-UM", "Universal Class A/B Module", True),
    ("NEXI300-3H-CGL-IPM", "Surface mounted Nexi Emergency Light, IP65", True),
    ("SL2-65D3D-CGL-M+SL23I", "Wall Mounted Exit, 20 metre viewing distance, IP65", True),

    # The panel's own parts.
    ("4-CPU", "Central Processor Module", False),
    ("4-FWAL4", "Firewall W/DACT/ EMAIL/DIAG", False),
    ("4-NET-TP", "SFP Network Controller, 2Mbps Shared TX/RX, Twisted Pair", False),
    ("4-USBHUB", "Multi-port USB Hub module", False),
    ("4-MIC", "Paging Microphone", False),
    ("4-CAB24D", "Door Assembly - Bronze outer door and black inner door", False),
    ("4-FIL", "Blank Filler Plate", False),
    ("4-COMREL", "Common Relay Module", False),
    ("4-AUDTELS", "Audio and Telephone Interface/Riser Module", False),
    ("4-FT", "Firefighter Master Handset", False),
    ("SIGA-AA50", "Intelligent Audio Amplifier - 50 Watt", False),
    ("3-SDDC2", "Dual loop SIGA Data Controller", False),
    ("FSB-PC4", "EST4 to BMS Communications Bridge", False),
    ("CTR400CGL2KS-M", "Menvier Brand CGLine+ Web Compact Controller", False),

    # Ordered with a device, never instead of it.
    ("27193-11", "Surface Mount Box - Indoor, RED, 1-gang", False),
    ("757A-WB", "Weatherproof Box, Cast - RED", False),
    ("SIGA-SB", "Signature Detector Base", False),
    ("SIGA-IB", "Detector Base with Isolator", False),
    ("GRSW-10", "Universal Wiring Plate - 10Packs", False),
    ("STI-3002", "Weatherresistant Gasket", False),
    ("STI-1230", "Stopper II without Horn - for Surface Stations", False),
    ("SD-T42", "Duct Detector Accessory, 42 in. Sampling Tube", False),
    ("TCS-6", "Storage Enclosure for 6830 handsets (6)", False),
    ("6830-3", "Portable (Plug-in) Telephone Handset - BLACK", False),
    ("SL210DI", "Pictogram", False),

    # Sized by the battery calculation, or bought by the metre.
    ("ES65-12", "12V 65Ah sealed lead acid battery", False),
    ("W-100", "2 core 1.5mm fire rated cable", False),
]


def test_only_field_devices_are_offered():
    wrong = [
        (part, description, "offered" if is_device(part, description) else "withheld")
        for part, description, wanted in REAL_MATERIALS
        if is_device(part, description) is not wanted
    ]
    assert wrong == []


def test_the_audible_base_is_not_a_device_of_its_own():
    """SIGA-LPS is half of "Smoke with Sounder Base", not a line on a
    floor: a base is ordered with the detector it sits under."""
    assert is_sounder_base("SIGA-LPS", "Audible (Sounder) Base")
    assert not is_device("SIGA-LPS", "Audible (Sounder) Base")
    assert not is_sounder_base("SIGA-OSD-FCN", "Intelligent Photoelectric Smoke Detector")


def test_a_detector_and_its_audible_base_are_offered_as_one_choice(client, db_session):
    """The schedule's "Smoke with Sounder Base" is two parts ordered
    together and counted once, so the pair is a choice of its own --
    settling the line as the detector alone would lose the base."""
    from app.core.config import get_settings
    from app.models import Project, ProjectProposedMaterial
    from app.services.schedule_materials import device_materials

    from .conftest import login

    settings = get_settings()
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = client.post("/projects", json={"ep_number": "30897", "project_name": "T",
                                                "design_sheets": []}).json()["id"]
    for part, description in [("SIGA-OSD-FCN", "Intelligent Photoelectric Smoke Detector"),
                              ("SIGA-LPS", "Audible (Sounder) Base"),
                              ("SIGA-SB", "Signature Detector Base")]:
        db_session.add(ProjectProposedMaterial(project_id=project_id, system_code="FAS", catalog_no=part,
                                               description=description, manufacturer="EDWARDS"))
    db_session.commit()

    offered = device_materials(db_session, db_session.get(Project, project_id), "FAS")
    parts = [material["part_no"] for material in offered]
    assert "SIGA-OSD-FCN" in parts
    assert "SIGA-OSD-FCN + SIGA-LPS" in parts
    # The bases are not offered alone.
    assert "SIGA-LPS" not in parts and "SIGA-SB" not in parts

    pair = next(material for material in offered if material["part_no"] == "SIGA-OSD-FCN + SIGA-LPS")
    assert pair["source"] == "pair" and "sounder" in pair["description"].lower()
