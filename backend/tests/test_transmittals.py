"""Sample boards read off the transmittals in a project's Transmittal
folder: the Word reader (.doc and .docx), what a transmittal says was
sent, the numbering of successive submissions, and the check that every
system of the project has had its sample board."""

import struct
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from app.services import transmittals
from app.services.word_text import WordReadError, cells, read_word_text

from .test_document_sync import ai  # noqa: F401 -- the fixture

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


# --- building Word documents ---------------------------------------------------------------


def _docx(path: Path, header: list[list[str]], paragraphs: list[str], items: list[list[str]]) -> Path:
    """A .docx the way the transmittal template lays it out: a header
    table, the letter's paragraphs, and the item table."""
    def table(rows):
        return "<w:tbl>" + "".join(
            "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{escape(c)}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
            for row in rows) + "</w:tbl>"
    body = table(header) + "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs) + table(items)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


def _doc(path: Path, text: str) -> Path:
    """A Word 97 binary .doc: an OLE compound file holding a WordDocument
    stream with the text (one byte a character) and a 1Table stream with
    the one-piece piece table pointing at it. Table cells are \\x07-ended,
    paragraphs \\r-ended, as Word writes them."""
    free, end, fat_sector = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    encoded = text.encode("cp1252")
    word = bytearray(4096)
    struct.pack_into("<H", word, 0, 0xA5EC)
    struct.pack_into("<H", word, 0x0A, 0x0200)             # the table stream is 1Table
    struct.pack_into("<II", word, 0x01A2, 0, 21)            # fcClx, lcbClx: the Clx is 1 + 4 + 16 bytes
    word[0x800:0x800 + len(encoded)] = encoded
    table = bytearray(4096)
    table[0] = 0x02
    struct.pack_into("<I", table, 1, 16)
    struct.pack_into("<II", table, 5, 0, len(encoded))      # the CPs
    struct.pack_into("<HIH", table, 13, 0, (0x800 * 2) | 0x40000000, 0)   # a compressed piece
    fat = [fat_sector, end] + list(range(3, 10)) + [end] + list(range(11, 18)) + [end]
    fat += [free] * (128 - len(fat))

    def entry(name: str, kind: int, start: int, size: int) -> bytes:
        raw = bytearray(128)
        encoded_name = (name + "\0").encode("utf-16-le") if name else b""
        raw[:len(encoded_name)] = encoded_name
        struct.pack_into("<H", raw, 0x40, len(encoded_name))
        raw[0x42] = kind
        struct.pack_into("<III", raw, 0x44, free, free, free)
        struct.pack_into("<II", raw, 0x74, start, size)
        return bytes(raw)

    directory = (entry("Root Entry", 5, end, 0) + entry("WordDocument", 2, 2, 4096)
                 + entry("1Table", 2, 10, 4096) + entry("", 0, free, 0))
    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIII", header, 0x2C, 1, 1, 0, 4096)   # FAT sectors, first directory sector, -, mini cutoff
    struct.pack_into("<IIII", header, 0x3C, end, 0, end, 0)
    struct.pack_into("<109I", header, 0x4C, 0, *([free] * 108))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header) + struct.pack("<128I", *fat) + directory + bytes(word) + bytes(table))
    return path


HEADER = [["To", ":", "M/s. Contractor", "Date", ":", "02/09/2026"],
          ["Attn.", ":", "Mr. Engineer", "AASS Ref.", ":", "TR/230/26"],
          ["Subject", ":", "Sample Board / Fire Alarm & Voice Evacuation & Central Battery System -EATON"]]
LETTER = ["Dear Sir/ Madam,", "With reference to the above subject, please find attached herewith sample Board detail:"]
ITEMS = [["ITEM", "Drawing No.", "Description", "Qty"],
         ["1", "---", "Sample Board / Fire Alarm & Voice Evacuation", "1 No."],
         ["2", "---", "Sample Board / Central Battery System -EATON", "3 Nos"]]


# --- the Word reader -------------------------------------------------------------------------


def test_a_docx_and_a_doc_read_to_the_same_cells(tmp_path):
    docx = _docx(tmp_path / "t.docx", HEADER, LETTER, ITEMS)
    # The same transmittal as Word 97 writes it: rows run on, cells end in \x07.
    raw = ("".join("\x07".join(row) + "\x07\x07" for row in HEADER) + "\r".join(LETTER) + "\r"
           + "".join("\x07".join(row) + "\x07\x07" for row in ITEMS) + "\r")
    doc = _doc(tmp_path / "t.doc", raw)
    assert cells(read_word_text(docx)) == cells(read_word_text(doc))
    assert cells(read_word_text(doc))[:6] == ["To", ":", "M/s. Contractor", "Date", ":", "02/09/2026"]


def test_what_is_not_a_word_document_says_so(tmp_path):
    other = tmp_path / "x.doc"
    other.write_bytes(b"%PDF-1.7 not a word document")
    with pytest.raises(WordReadError):
        read_word_text(other)


# --- what a transmittal sent -----------------------------------------------------------------


def _read(header_subject: str, items: list[list[str]] | None = None, *, ref="TR/230/26", date="02/09/2026"):
    header = [["Date", ":", date], ["AASS Ref.", ":", ref], ["Subject", ":", header_subject]]
    text = "\n".join("|" + " |".join(row) + " |" for row in header) + "\n" + "\n".join(LETTER) + "\n"
    text += "\n".join("|" + " |".join(row) + " |" for row in (items or []))
    return transmittals.read_transmittal(text, "Transmittal/x.doc", NOW)


def test_each_item_that_is_a_sample_board_is_a_submission_for_its_system():
    rows = _read(HEADER[2][2], ITEMS)
    assert [(r.name, r.system_code) for r in rows] == [("Sample Board", "VES"), ("Sample Board", "FAS"), ("Sample Board", "ELS")]
    first = rows[0]
    assert (first.reference, first.modified, first.category, first.source, first.status) == (
        "TR/230/26", datetime(2026, 9, 2, tzinfo=timezone.utc), "samples", "transmittal", "UR")


@pytest.mark.parametrize("subject, items, expected", [
    # Items listing part numbers: the subject names the system.
    ("Sample Board / Fire Alarm System", [["1", "SIGA-OSD", "Intelligent Photoelectric Smoke Detector", "1"]],
     [("Sample Board", "FAS")]),
    # A heading row inside the item table names the board.
    ("Sample Board / Fire Alarm & Emergency Lighting System",
     [["", "", "Emergency Lighting Sample Board", ""], ["1", "SL2", "Safe lite Exit light", "1"],
      ["", "", "Fire Alarm Sample Board -Edwards", ""]],
     [("Sample Board", "ELS"), ("Sample Board", "FAS")]),
    # "Sample Board | BGM System": the system in the next cell.
    ("Material Submittal & Sample Board / BGM System", [["4", "Sample Board", "BGM System", "1 no."]],
     [("Sample Board", "PAVA")]),
    # Two clauses in one subject.
    ("Sample Board / Emergency Lighting System -Ropag & Sample Material / Fire Alarm System -Detector", [],
     [("Sample Board", "ELS"), ("Sample Material", "FAS")]),
    # A board of cables is the fire-rated cable's, whatever systems they serve.
    ("Sample Board / Fire Rated Cables for Fire Alarm & Emergency Lighting System", [], [("Sample Board", "FRC")]),
    # Typos the forms carry, and a sample named by its device.
    ("Sample Baord / Central Battery System", [], [("Sample Board", "ELS")]),
    ("Sample Material / Manual Call Point", [], [("Sample Material", "FAS")]),
    # Not the platform's systems, or not a sample at all.
    ("Sample Board / CCTV System -HIKVISION", [], []),
    ("Sample Board / Speaker- TOA", [], []),
    ("Technical Submittal / Public Address System", [["1", "EP-21152/AF/PA/201", "Technical Submittal / Public Address System", "1"]], []),
    ("Material Submittal / Fire Alarm & Voice Evacuation System", [], []),
])
def test_what_a_transmittal_says_it_sent(subject, items, expected):
    assert [(r.name, r.system_code) for r in _read(subject, items)] == expected


def test_the_attached_herewith_sentence_names_no_sample():
    # "please find attached herewith sample board details" is in every letter.
    assert _read("Material Submittal / Fire Alarm System", []) == []


def test_only_word_documents_in_a_transmittal_folder_are_transmittals():
    assert transmittals.is_transmittal("Transmittal/EP-29495 FA Sam B.doc")
    assert transmittals.is_transmittal("EP-16214 - ELV/EP-16214 - Transmittal/Sample.docx")
    assert transmittals.is_transmittal("05- Transmittals/sub/x.DOC")
    assert not transmittals.is_transmittal("03- MS/Sample Board.doc")         # not the Transmittal folder
    assert not transmittals.is_transmittal("Transmittal/scan.pdf")            # not a Word document
    assert not transmittals.is_transmittal("Transmittal/~$Sam B.doc")          # Word's lock file
    assert not transmittals.is_transmittal("Transmittal.doc")                 # the file, not a folder


# --- successive submissions --------------------------------------------------------------------


def test_submissions_are_numbered_in_the_order_sent_and_copies_are_one():
    first = _read("Sample Board / Fire Alarm System", ref="TR/100/25", date="01/03/2025")
    again = [replace(r, path="Transmittal/x-DSTHBGLJ32.doc") for r in first]   # a conflict copy
    later = _read("Sample Board / Fire Alarm System", ref="TR/300/25", date="10/06/2025")
    other = _read("Sample Board / Central Battery System", ref="TR/300/25", date="10/06/2025")
    rows = sorted(transmittals.number(later + again + first + other), key=lambda r: (r.system_code, r.revision))
    assert [(r.system_code, r.revision, r.reference, r.status) for r in rows] == [
        ("ELS", "R0", "TR/300/25", "UR"),
        ("FAS", "R0", "TR/100/25", "SUPERSEDED"),
        ("FAS", "R1", "TR/300/25", "UR"),
    ]
    assert rows[1].path == "Transmittal/x.doc"   # the original, not the copy


# --- through the sync, to the log ----------------------------------------------------------------


def test_a_synced_project_logs_its_sample_boards_and_names_the_system_without_one(client, tmp_path, ai):  # noqa: F811
    from .conftest import login
    from app.core.config import get_settings

    settings = get_settings()
    folder = tmp_path / "EP-29495"
    _docx(folder / "Transmittal" / "EP-29495 FA Sam B.docx", HEADER, LETTER, ITEMS)
    # Not in the Transmittal folder: not read, whatever it says.
    _docx(folder / "Letters" / "PA Sample Board.docx",
          [["Subject", ":", "Sample Board / Public Address System"]], [], [])
    login(client, settings.default_admin_email, settings.default_admin_password)
    project_id = client.post("/projects", json={
        "ep_number": "29495", "project_name": "IVY Garden", "source_folder_path": str(folder), "design_sheets": [],
        "systems": [{"name": "Fire Alarm", "brand": "Edwards", "method_statement": True, "drawing": True},
                    {"name": "Voice Evacuation", "brand": "Edwards", "method_statement": True, "drawing": True},
                    {"name": "Central Battery System", "brand": "Eaton", "method_statement": True, "drawing": True},
                    {"name": "PA/VA & BGM", "brand": "TOA", "method_statement": True, "drawing": True}],
    }).json()["id"]

    assert client.get(f"/projects/{project_id}/logs").json()["sample_boards"] == []   # nothing said before a sync
    started = client.post(f"/projects/{project_id}/jobs/sync-documents")
    assert started.status_code == 202, started.text
    assert started.json()["result"]["files"] == 1

    logs = client.get(f"/projects/{project_id}/logs").json()
    # The Edwards fire alarm carries the voice evacuation: one fire alarm board, not two.
    assert sorted((s["system_code"], s["revision"], s["reference"]) for s in logs["samples"]) == [
        ("ELS", "R0", "TR/230/26"), ("FAS", "R0", "TR/230/26")]
    assert all(s["path"] == "Transmittal/EP-29495 FA Sam B.docx" for s in logs["samples"])
    checks = {c["system_code"]: c for c in logs["sample_boards"]}
    assert set(checks) == {"FAS", "PAVA", "ELS"}
    assert checks["FAS"]["state"] == "submitted" and checks["FAS"]["reference"] == "TR/230/26"
    assert checks["FAS"]["submitted_on"].startswith("2026-09-02")
    assert checks["ELS"]["state"] == "submitted"
    assert checks["PAVA"]["state"] == "missing" and checks["PAVA"]["reference"] is None
