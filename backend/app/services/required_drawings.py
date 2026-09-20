"""What the contractor must hand over before a system's shop drawings can
start, and whether it has been: Drawings > Actions Required, one list per
system (fire alarm, emergency lighting).

Each item is a folder of the project's own structure (`project_folders`),
in the project folder that OneDrive keeps in step. An item is received
when its folder holds a file; a folder that is empty or not there is not
received -- nothing is ticked by hand, and filing the contractor's file in
its folder is what receives it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.services import document_control
from app.services.project_folders import DRAWINGS


@dataclass(frozen=True)
class Item:
    key: str
    system: str
    group: str
    name: str
    purpose: str
    folder: str
    format: str = ""
    note: str = ""        # what the remarks say while nothing is filed


SYSTEMS = {"FAS": "Fire Alarm", "ELS": "Emergency Lighting"}

GROUPS = {
    "FAS": {"electrical": "Electrical IFC Drawings", "mechanical": "Mechanical IFC Drawings", "others": "Others"},
    "ELS": {"electrical": "Electrical Drawings", "others": "Others"},
}

ITEMS: tuple[Item, ...] = (
    Item("fa_ifc", "FAS", "electrical", "Fire Alarm IFC Drawings", "Main background for fire alarm shop drawings",
         f"{DRAWINGS}/IFC/Electrical/FA", "IFC / DWG"),
    Item("acs_ifc", "FAS", "electrical", "Access Control System", "Interface coordination",
         f"{DRAWINGS}/IFC/Electrical/ACS", "IFC / DWG"),
    Item("ff_ifc", "FAS", "mechanical", "Fire Fighting IFC Drawings", "Pump room and interface coordination",
         f"{DRAWINGS}/IFC/Mechanical/FF", "IFC / DWG"),
    Item("sm_ifc", "FAS", "mechanical", "Smoke Management IFC Drawings", "Cause & effect and interface coordination",
         f"{DRAWINGS}/IFC/Mechanical/SM", "IFC / DWG"),
    Item("title_block", "FAS", "others", "Title Block", "Required for drawing format and submission standard",
         f"{DRAWINGS}/Title Block", "DWG"),
    Item("sd_reference", "FAS", "others", "Shop Drawings Reference Number", "Required for document control and submission",
         f"{DRAWINGS}/SD Reference No", "PDF / XLS"),
    # The fire alarm list's own folder: one drawing filed there is received for both systems.
    Item("els_fa_ifc", "ELS", "electrical", "Fire Alarm IFC Drawings",
         "Fire alarm layout for coordination with emergency lighting: device positions, routes and interfaces.",
         f"{DRAWINGS}/IFC/Electrical/FA", "IFC / DWG", "Required for ELS and fire alarm coordination"),
    Item("els_lighting_ifc", "ELS", "electrical", "Lighting IFC Drawings",
         "IFC model including lighting layout, circuits, DB locations and interface details.",
         f"{DRAWINGS}/IFC/Electrical/Light", "IFC / DWG", "Required for ELS design coordination"),
    Item("els_load_schedule", "ELS", "others", "Load Schedule",
         "Electrical load schedule for all DBs including essential, normal and emergency loads.",
         f"{DRAWINGS}/IFC/Electrical/Load Schedule", "PDF / XLS", "Required for ELS load and battery sizing"),
)
BY_KEY = {i.key: i for i in ITEMS}

# What a folder holds that is not a document: OneDrive's and Windows' own files,
# Office's and AutoCAD's lock files (a drawing open in AutoCAD leaves a .dwl), backups.
_NOT_A_FILE = {"desktop.ini", "thumbs.db", ".ds_store"}
_NOT_A_DOCUMENT = (".dwl", ".dwl2", ".bak", ".tmp", ".sv$", ".ac$", ".lck")


def _files(folder: Path) -> list[tuple[str, float]]:
    """(path relative to the folder, modified) of every file under it."""
    out = []
    for dirpath, _dirs, names in os.walk(document_control._os_path(folder)):
        for name in names:
            if name.lower() in _NOT_A_FILE or name.startswith("~$") or name.lower().endswith(_NOT_A_DOCUMENT):
                continue
            full = os.path.join(dirpath, name)
            try:
                modified = os.path.getmtime(full)
            except OSError:
                continue
            rel = os.path.relpath(full, document_control._os_path(folder)).replace("\\", "/")
            out.append((rel, modified))
    return sorted(out, key=lambda f: -f[1])


def status(project, requests: dict[str, datetime] | None = None, system: str = "FAS") -> dict:
    """Every required item of a system with where it stands. `requests`:
    item key -> when it was last requested from the contractor."""
    requests = requests or {}
    root = Path(project.source_folder_path) if project.source_folder_path else None
    reachable = root is not None and os.path.isdir(document_control._os_path(root))
    items = []
    for item in (i for i in ITEMS if i.system == system):
        folder = root / item.folder if root else None
        exists = bool(folder) and reachable and os.path.isdir(document_control._os_path(folder))
        files = _files(folder) if exists else []
        requested = requests.get(item.key)
        if files:
            latest, _modified = files[0]
            remarks = f"1 file: {Path(latest).name}" if len(files) == 1 else f"{len(files)} files, latest {Path(latest).name}"
        elif not reachable:
            remarks = "The project folder is not reachable on this PC"
        elif not exists:
            remarks = "Its folder is not in the project folder yet"
        else:
            remarks = item.note or "Folder empty"
        if not files and requested and not item.note:
            remarks = f"Requested from the contractor {requested:%d %b %Y}; {remarks[0].lower()}{remarks[1:]}"
        items.append({
            "key": item.key, "group": item.group, "name": item.name, "purpose": item.purpose, "folder": item.folder,
            "format": item.format,
            "received": bool(files),
            "received_date": datetime.fromtimestamp(files[0][1]).isoformat(timespec="seconds") if files else None,
            "files": [{"path": f"{item.folder}/{rel}", "name": Path(rel).name,
                       "modified": datetime.fromtimestamp(m).isoformat(timespec="seconds")} for rel, m in files[:50]],
            "file_count": len(files),
            "folder_exists": exists,
            "requested_at": requested.isoformat(timespec="seconds") if requested else None,
            "remarks": remarks,
        })
    received = sum(1 for i in items if i["received"])
    return {
        "system": system, "system_name": SYSTEMS[system],
        "groups": [{"key": k, "name": v, "items": [i for i in items if i["group"] == k]} for k, v in GROUPS[system].items()],
        "total": len(items), "received": received, "not_received": len(items) - received,
        "reachable": reachable, "contractor": project.contractor,
    }


def request_text(project, keys: list[str]) -> dict:
    """The email asking the contractor for the items."""
    items = [BY_KEY[k] for k in keys if k in BY_KEY]
    title = f"EP-{project.ep_number} {project.project_name or ''}".strip()
    systems = " and ".join(dict.fromkeys(SYSTEMS[i.system].lower() for i in items)) or "fire alarm"
    lines = [f"Dear {project.contractor or 'Sir/Madam'},", "",
             f"To start the {systems} shop drawings for {title}, kindly provide the following:", ""]
    for n, item in enumerate(items, 1):
        lines.append(f"{n}. {item.name} ({item.format}) -- {item.purpose}" if item.format else f"{n}. {item.name} -- {item.purpose}")
    lines += ["", "Your early response is appreciated.", "", "Best regards,"]
    return {"subject": f"{title} - Documents required for {systems} shop drawings", "body": "\n".join(lines),
            "items": [i.name for i in items]}
