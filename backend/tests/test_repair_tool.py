"""scripts/repair_extraction.py on a temporary database: a stored reading
the earlier parser got wrong (a date for a reference, a reference cut at
a slash) is previewed, repaired, left alone on a second run, and put back
by a rollback; the form reading, the row's manual fields and the history
stay; a row whose file changed is skipped and named."""

from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import pymupdf

from app.core.config import get_settings
from app.models import DocumentClassification, DocumentDependency, Project, ProjectDocument
from app.services import document_control, document_sync

from .test_document_sync import _project, ai  # noqa: F401 -- fixture
from .test_extraction_repair import COVER, REPLY_PAGE
from .test_file_sync_v2_processing import _rows

settings = get_settings()


def _tool():
    spec = importlib.util.spec_from_file_location("repair_extraction", Path(__file__).resolve().parent.parent / "scripts" / "repair_extraction.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pdf(path: Path, pages: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        for text in pages:
            document.new_page().insert_text((30, 30), text, fontsize=7)
        document.save(path)
    return path


def _wrong_reading(row: ProjectDocument) -> None:
    """What the earlier parser stored for such a cover: a date for a MAR
    reference, category submittals, and a reply cut at the slash."""
    relative = row.relative_path
    row.extracted = {"records": [
        {"system_code": "FAS", "name": "6-Mar-2026", "path": relative, "modified": "2026-07-10T05:22:17+00:00",
         "reference": "6-Mar-2026", "revision": "R1", "status": "UR", "floor": None, "reply_text": None, "page": 1,
         "source": "document", "category": "submittals", "group_reference": None},
        {"system_code": None, "name": "Reply to consultant comments", "path": relative, "modified": "2026-07-10T05:22:17+00:00",
         "reference": "ABC-XYZ-SPM-SD-MEP", "revision": "R0", "status": "UR", "floor": None, "reply_text": None, "page": 2,
         "source": "reply", "category": "reply", "group_reference": None}],
        "notes": [], "parser_version": "parse-earlier", "form": {"is_submittal": False, "kept": "as the model read it"}}
    row.reference, row.revision, row.status = "6-Mar-2026", "R1", "UR"


def test_the_repair_previews_applies_once_and_rolls_back(client, db_session, tmp_path, ai, monkeypatch, capsys):
    monkeypatch.setattr(settings, "document_classification_v2", True)
    folder = tmp_path / "EP-30940"
    path = _pdf(folder / "3. SHOP DRAWING" / "FIRE ALARM" / "ABC-XYZ-SPM-SD-MEP-FA-0054-01-COMMENTED-C.pdf", [COVER, REPLY_PAGE])
    _pdf(folder / "05- Drawings" / "L01.pdf", ["Drawing title\nBBY006-GME-SDW-EL-FA-0001\nREV. 01\nGround Floor Layout"])
    project_id = _project(client, folder, "30940")
    job = client.post(f"/projects/{project_id}/jobs/sync-documents").json()
    assert job["status"] == "succeeded"
    rows = _rows(db_session, project_id)
    cover = rows["3. SHOP DRAWING/FIRE ALARM/ABC-XYZ-SPM-SD-MEP-FA-0054-01-COMMENTED-C.pdf"]
    assert cover.extracted["records"][0]["reference"] == "ABC-XYZ-SPM-SD-MEP-FA-0054", "the parser as it is now reads it right"
    # As the earlier parser left it.
    _wrong_reading(cover)
    cover.acknowledged = [{"note": "kept"}]
    db_session.commit()
    before_history = db_session.query(DocumentClassification).count()
    tool = _tool()

    # Dry run: the manifest says what would change; nothing changes.
    manifest = tmp_path / "dry.json"
    assert tool.main(["--project", str(project_id), "--select", "date-references,truncated-references", "--manifest", str(manifest), "--no-ocr"]) == 0
    dry = json.loads(manifest.read_text(encoding="utf-8"))
    assert dry["mode"] == "dry-run" and dry["selected"] == 1 and dry["counts"] == {"would_repair": 1}
    [entry] = dry["entries"]
    assert entry["document_id"] == cover.id and "reference is a date" in " ".join(entry["reasons"])
    assert entry["old"]["reference"] == "6-Mar-2026" and entry["new"]["reference"] == "ABC-XYZ-SPM-SD-MEP-FA-0054"
    assert entry["new"]["revision"] == "R1" and entry["changed"]["records"] and entry["changed"]["reference"]
    # The reply is folded into the submission and, since M2, kept as a record of its own too.
    assert [r["category"] for r in entry["new"]["records"]] == ["drawings", "reply"]
    assert "shop_drawing_submittal" in entry["new"]["evidence_kinds"]
    db_session.expire_all()
    assert db_session.get(ProjectDocument, cover.id).reference == "6-Mar-2026", "a dry run writes nothing"

    # Apply: the reading is replaced, the form reading and the manual field kept, the classification redone.
    applied = tmp_path / "apply.json"
    assert tool.main(["--project", str(project_id), "--select", "date-references,truncated-references", "--apply", "--reassess",
                      "--manifest", str(applied), "--no-ocr"]) == 0
    result = json.loads(applied.read_text(encoding="utf-8"))
    assert result["counts"] == {"repaired": 1}
    db_session.expire_all()
    repaired = db_session.get(ProjectDocument, cover.id)
    assert repaired.reference == "ABC-XYZ-SPM-SD-MEP-FA-0054" and repaired.revision == "R1"
    assert repaired.extracted["parser_version"] == document_control.PARSER_VERSION
    assert repaired.extracted["form"] == {"is_submittal": False, "kept": "as the model read it"}, "the model's reading stays"
    assert repaired.acknowledged == [{"note": "kept"}] and repaired.state == "fresh"
    assert [r["category"] for r in repaired.extracted["records"]] == ["drawings", "reply"], "the folded reply page is kept as a record (M2)"
    assert repaired.extracted["records"][0]["listed"] == ["ABC-XYZ-SPM-SD-MEP/FA-104 A~104 M"]
    assert result["entries"][0]["reassessment"]["after"]["type"] == "SHOP_DRAWING"
    current = (db_session.query(DocumentClassification)
               .filter(DocumentClassification.document_id == cover.id, DocumentClassification.superseded_at.is_(None)).one())
    assert current.primary_type == "SHOP_DRAWING" and current.stage == "supported"
    assert db_session.query(DocumentClassification).count() > before_history, "history kept, not rewritten"
    links = {(d.dependent_type, d.dependent_id): d.stale for d in db_session.query(DocumentDependency)
             .filter(DocumentDependency.source_document_id == cover.id)}
    assert links.get(("log", "ABC-XYZ-SPM-SD-MEP-FA-0054")) is False
    # The other row was never selected.
    other = _rows(db_session, project_id)["05- Drawings/L01.pdf"]
    assert other.extracted["records"][0]["reference"] == "BBY006-GME-SDW-EL-FA-0001"

    # Again: nothing to do.
    again = tmp_path / "again.json"
    assert tool.main(["--project", str(project_id), "--select", "date-references,truncated-references,parser-outdated", "--apply",
                      "--manifest", str(again), "--no-ocr"]) == 0
    assert json.loads(again.read_text(encoding="utf-8"))["selected"] == 0
    everything = tmp_path / "all.json"
    assert tool.main(["--project", str(project_id), "--select", "all", "--apply", "--manifest", str(everything), "--no-ocr"]) == 0
    assert json.loads(everything.read_text(encoding="utf-8"))["counts"] == {"unchanged": 2}

    # Rollback: the earlier reading is back, the row untouched since.
    assert tool.main(["--rollback", str(applied)]) == 0
    db_session.expire_all()
    restored = db_session.get(ProjectDocument, cover.id)
    assert restored.reference == "6-Mar-2026" and restored.extracted["parser_version"] == "parse-earlier"
    assert restored.acknowledged == [{"note": "kept"}]
    # A second rollback finds the row changed since (by the first) and skips it.
    assert tool.main(["--rollback", str(applied)]) == 0
    out = capsys.readouterr().out
    assert '"skipped": [{"document_id": ' in out.splitlines()[-1]


def test_a_changed_or_unfresh_file_is_skipped_and_named(client, db_session, tmp_path, ai, monkeypatch):
    folder = tmp_path / "EP-30941"
    path = _pdf(folder / "3. SHOP DRAWING" / "X-SD-MEP-FA-0001-00.pdf", [COVER])
    project_id = _project(client, folder, "30941")
    assert client.post(f"/projects/{project_id}/jobs/sync-documents").json()["status"] == "succeeded"
    row = _rows(db_session, project_id)["3. SHOP DRAWING/X-SD-MEP-FA-0001-00.pdf"]
    _wrong_reading(row)
    db_session.commit()
    tool = _tool()
    # The file changed after the index read it: not repaired, a sync will read it.
    _pdf(path, [COVER + "\nchanged"])
    later = time.time() + 60
    os.utime(path, (later, later))
    manifest = tmp_path / "m.json"
    assert tool.main(["--ids", str(row.id), "--apply", "--manifest", str(manifest), "--no-ocr"]) == 0
    entry = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    assert entry["outcome"] == "skipped" and "not the content the row was read from" in entry["skip_reason"]
    db_session.expire_all()
    assert db_session.get(ProjectDocument, row.id).reference == "6-Mar-2026"
    # A pending row is not repaired either: its reading is for the previous content.
    row = db_session.get(ProjectDocument, row.id)
    row.state = document_sync.PENDING
    db_session.commit()
    assert tool.main(["--ids", str(row.id), "--apply", "--manifest", str(manifest), "--no-ocr"]) == 0
    entry = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    assert entry["outcome"] == "skipped" and "only a fresh reading" in entry["skip_reason"]


def test_a_transmittal_and_an_unreadable_file_are_not_repaired(client, db_session, tmp_path, ai, monkeypatch):
    from types import SimpleNamespace

    tool = _tool()
    transmittal = SimpleNamespace(id=1, project_id=1, relative_path="12. Transmittal/T.doc", filename="T.doc", role="transmittal",
                                  state="fresh", sha256="x", reference="TR/1/26", revision="R0", status="UR",
                                  extracted={"records": [{"category": "samples", "reference": "TR/1/26"}]})
    entry = tool.preview(db_session, transmittal, ["selected by id"], False)
    assert entry["outcome"] == "skipped" and "transmittal reader" in entry["skip_reason"]
    # A PDF that cannot be read now keeps its full reading.
    folder = tmp_path / "EP-30942"
    path = _pdf(folder / "3. SHOP DRAWING" / "X-SD-MEP-FA-0001-00.pdf", [COVER])
    project_id = _project(client, folder, "30942")
    assert client.post(f"/projects/{project_id}/jobs/sync-documents").json()["status"] == "succeeded"
    row = _rows(db_session, project_id)["3. SHOP DRAWING/X-SD-MEP-FA-0001-00.pdf"]
    _wrong_reading(row)
    db_session.commit()
    monkeypatch.setattr(tool.document_sync, "extract",
                        lambda *a, **k: ("document", (), (f"{document_control.UNREADABLE}{path.name}.",), {"total_ms": 1}))
    entry = tool.preview(db_session, db_session.get(ProjectDocument, row.id), ["selected by id"], False)
    assert entry["outcome"] == "skipped" and "could not be read now" in entry["skip_reason"]
