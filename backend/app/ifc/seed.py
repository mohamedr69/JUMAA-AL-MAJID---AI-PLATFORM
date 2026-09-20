"""The device types a fire alarm IFC symbol can be verified as.

The fire alarm list of the original tool, with the fire alarm devices its
users added. Emergency lighting is not here yet: it is the next step, and
the devices the original library had filed as fire alarm that are really
emergency lighting (exit signs, an EM panel) wait for it. More are added
from any device dropdown, and are saved for good.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import IfcDeviceType

FIRE_ALARM = [
    ("SD", "Smoke Detector (Addressable)"),
    ("HD", "Heat Detector (Addressable)"),
    ("MD", "Multi-Sensor Detector (Smoke + Heat)"),
    ("SDS", "Smoke Detector with Sounder Base"),
    ("HDS", "Heat Detector with Sounder Base"),
    ("BD", "Beam Detector"),
    ("DD", "Duct Smoke Detector"),
    ("ASD", "Aspirating Smoke Detector"),
    ("FD", "Flame Detector"),
    ("GD", "Gas Detector"),
    ("MCP", "Manual Call Point (Break Glass)"),
    ("MCP-WP", "Manual Call Point Weatherproof"),
    ("SND", "Sounder"),
    ("SNS", "Sounder with Strobe / Flasher"),
    ("SNS-WP", "Sounder with Strobe Weatherproof"),
    ("STB", "Strobe / Flasher"),
    ("SPK", "Ceiling Speaker"),
    ("SPKW", "Wall Speaker"),
    ("SPKS", "Speaker with Strobe / Flasher"),
    ("FACP", "Fire Alarm Control Panel"),
    ("RP", "Repeater Panel / Annunciator"),
    ("MIM", "Monitor / Input Module"),
    ("CM", "Control / Output Module"),
    ("ISO", "Loop Isolator Module"),
    ("RI", "Remote Indicator"),
    ("FTJ", "Fireman Telephone Jack"),
    ("FTH", "Fireman Telephone Handset"),
    ("DH", "Door Holder (Magnetic)"),
    ("FS", "Flow Switch Interface"),
    ("TS", "Tamper Switch Interface"),
    ("PSU", "Power Supply Unit"),
    ("VEP", "Voice Evacuation Panel"),
    ("EOL", "End of Line Device"),
]

# Added by the original tool's users, kept as they named them.
FIRE_ALARM_ADDED = [
    ("CSF", "Ceiling Speaker with flasher"),
    ("SFW", "Wall Mounted Speaker Flasher WP"),
    ("SS", "Sounder Strobe WP"),
    ("VD", "Void smoke detector"),
    ("VE-HORN-SPKR", "WALL MOUNTED SPEAKER"),
]


def seed_device_types(db: Session) -> None:
    """The list, once: a database that has any device type is left alone,
    so a type an engineer renamed or hid stays as they left it."""
    if db.query(IfcDeviceType).count() > 0:
        return
    order = 0
    for code, name in FIRE_ALARM:
        order += 10
        db.add(IfcDeviceType(code=code, name=name, category="fire_alarm", sort_order=order))
    for code, name in FIRE_ALARM_ADDED:
        db.add(IfcDeviceType(code=code, name=name, category="fire_alarm", sort_order=500))
    db.commit()
