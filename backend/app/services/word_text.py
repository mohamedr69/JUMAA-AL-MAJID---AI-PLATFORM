"""The text of a Word document, .docx or the older binary .doc, with no
library beyond the standard one and nothing needing Word installed.

The company's transmittals are Word documents, and four in five of them
are still the Word 97 binary format. Both readers give the text in one
shape, which is all the transmittal reader relies on: paragraphs on
lines, table cells separated by " |" --

    Subject |: |Sample Board / Fire Alarm & Voice Evacuation System |

-- read as a flat run of cells (`cells`), because a .doc does not say
reliably where one table row ends and the next begins.

A .doc is an OLE compound file (a small FAT file system in one file); its
"WordDocument" stream holds the characters and a table stream (0Table or
1Table) holds the piece table saying where each run of text is and
whether it is stored one byte or two per character. Table cells end in
\\x07 and a row ends in one more; paragraphs end in \\r.
"""

from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from app.services.document_control import _os_path

WORD_SUFFIXES = {".doc", ".docx"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class WordReadError(Exception):
    pass


def cells(text: str) -> list[str]:
    """The text as a flat run of non-empty cells and paragraphs, the one
    shape a .doc and a .docx both reliably give."""
    return [c for c in (re.sub(r"\s+", " ", part).strip() for part in re.split(r"[|\n]", text)) if c]


def read_word_text(path: Path) -> str:
    """The document's text, or WordReadError when it cannot be read."""
    with open(_os_path(path), "rb") as handle:
        data = handle.read()
    if data[:4] == b"PK\x03\x04":
        return _docx_text(data)
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return _doc_text(data)
    raise WordReadError(f"{path.name} is not a Word document")


# --- .docx -------------------------------------------------------------------------------


def _docx_text(data: bytes) -> str:
    import io

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise WordReadError(f"unreadable .docx: {exc}") from exc
    body = root.find(f"{_W}body")
    lines: list[str] = []
    for child in body if body is not None else ():
        if child.tag == f"{_W}p":
            lines.append(_paragraph(child))
        elif child.tag == f"{_W}tbl":
            for row in child.iter(f"{_W}tr"):
                cells = [" ".join(_paragraph(p) for p in cell.iter(f"{_W}p")).strip()
                         for cell in row.findall(f"{_W}tc")]
                lines.append("|" + " |".join(cells) + " |")
    return "\n".join(lines)


def _paragraph(element) -> str:
    parts = []
    for node in element.iter():
        if node.tag == f"{_W}t":
            parts.append(node.text or "")
        elif node.tag in (f"{_W}tab", f"{_W}br"):
            parts.append(" ")
    return "".join(parts)


# --- .doc --------------------------------------------------------------------------------

_FREE, _END_OF_CHAIN = 0xFFFFFFFF, 0xFFFFFFFE


def _streams(data: bytes) -> dict[str, bytes]:
    """The streams of an OLE compound file, by name (top level only)."""
    if len(data) < 512:
        raise WordReadError("truncated compound file")
    sector_size = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    mini_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
    fat_count, first_dir = struct.unpack_from("<II", data, 0x2C)
    mini_cutoff, first_minifat, minifat_count, first_difat, difat_count = struct.unpack_from("<IIIII", data, 0x38)

    def sector(n: int) -> bytes:
        start = (n + 1) * sector_size
        return data[start:start + sector_size]

    fat_sectors = list(struct.unpack_from("<109I", data, 0x4C))
    n, seen = first_difat, 0
    while n not in (_FREE, _END_OF_CHAIN) and seen < difat_count:
        entries = struct.unpack(f"<{sector_size // 4}I", sector(n))
        fat_sectors.extend(entries[:-1])
        n, seen = entries[-1], seen + 1
    fat: list[int] = []
    for s in fat_sectors[:fat_count]:
        fat.extend(struct.unpack(f"<{sector_size // 4}I", sector(s)))

    def chain(start: int, table: list[int]) -> list[int]:
        found, n = [], start
        while n < len(table) and n not in (_FREE, _END_OF_CHAIN) and len(found) <= len(table):
            found.append(n)
            n = table[n]
        return found

    directory = b"".join(sector(s) for s in chain(first_dir, fat))
    entries = []
    for offset in range(0, len(directory) - 127, 128):
        name_len = struct.unpack_from("<H", directory, offset + 0x40)[0]
        kind = directory[offset + 0x42]
        start, size = struct.unpack_from("<II", directory, offset + 0x74)
        name = directory[offset:offset + max(name_len - 2, 0)].decode("utf-16-le", "replace")
        entries.append((name, kind, start, size))
    if not entries:
        raise WordReadError("no directory in compound file")
    _root_name, _root_kind, mini_start, mini_stream_size = entries[0]
    mini_stream = b"".join(sector(s) for s in chain(mini_start, fat))[:mini_stream_size]
    minifat: list[int] = []
    for s in chain(first_minifat, fat)[:minifat_count or None]:
        minifat.extend(struct.unpack(f"<{sector_size // 4}I", sector(s)))

    streams = {}
    for name, kind, start, size in entries[1:]:
        if kind != 2:   # a stream
            continue
        if size < mini_cutoff:
            body = b"".join(mini_stream[s * mini_size:(s + 1) * mini_size] for s in chain(start, minifat))
        else:
            body = b"".join(sector(s) for s in chain(start, fat))
        streams[name] = body[:size]
    return streams


def _doc_text(data: bytes) -> str:
    streams = _streams(data)
    word = streams.get("WordDocument")
    if not word or len(word) < 0x1AA or struct.unpack_from("<H", word, 0)[0] != 0xA5EC:
        raise WordReadError("not a Word 97 or later document")
    flags = struct.unpack_from("<H", word, 0x0A)[0]
    if flags & 0x0100:
        raise WordReadError("the document is encrypted")
    table = streams.get("1Table" if flags & 0x0200 else "0Table")
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)
    if not table or not lcb_clx or fc_clx + lcb_clx > len(table):
        raise WordReadError("no piece table")
    clx = table[fc_clx:fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 0x01:           # Prc: property modifiers, skipped
        i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
    if i >= len(clx) or clx[i] != 0x02:
        raise WordReadError("no piece table")
    lcb = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + lcb]
    pieces = (lcb - 4) // 12
    cps = struct.unpack_from(f"<{pieces + 1}I", plc, 0)
    parts = []
    for n in range(pieces):
        fc = struct.unpack_from("<I", plc, (pieces + 1) * 4 + n * 8 + 2)[0]
        count = cps[n + 1] - cps[n]
        if fc & 0x40000000:
            start = (fc & ~0x40000000) // 2
            parts.append(word[start:start + count].decode("cp1252", "replace"))
        else:
            parts.append(word[fc:fc + 2 * count].decode("utf-16-le", "replace"))
    return _doc_layout("".join(parts))


def _doc_layout(text: str) -> str:
    """Word's control characters to the shared shape: a cell mark to " |",
    a row's end to a line break, a paragraph mark to a line break; field
    codes (the part between \\x13 and \\x14) dropped, their result kept."""
    text = re.sub(r"\x13[^\x13\x14\x15]*\x14", "", text)
    text = re.sub(r"\x13[^\x13\x14\x15]*\x15", "", text)
    # A cell ends in \x07 and so does a row, with no paragraph mark after
    # it -- rows run on into each other, and an empty cell looks the same
    # as a row's end. So the cells are kept apart and the rows are not
    # recovered: a reader of this text works cell by cell (see cells()).
    text = text.replace("\x15", "").replace("\x07", " |").replace("\r", "\n")
    text = text.replace("\x0b", " ").replace("\x0c", "\n").replace("\x1e", "-").replace("\x1f", "").replace("\xa0", " ")
    return re.sub(r"[\x00-\x08\x0e-\x1d]", "", text)
