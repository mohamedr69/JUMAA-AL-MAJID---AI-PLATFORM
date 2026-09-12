"""Reading material submittals and the consultant's reply out of a project
folder.

The synthetic forms copy the real one (EP-30784's "Materials Submittal
Form"): the same headings, the same three checkboxes, and the reply as a
line of its own the way a consultant's stamp reads once OCR'd.
"""

import os
from pathlib import Path

import pymupdf
import pytest

from app.services.submittal_scanner import read_reply_code, scan_folder

LIVE_ROOT = os.environ.get("EP_PLATFORM_LIVE_ARCHIVE_ROOT")
requires_live_archive = pytest.mark.skipif(
    not LIVE_ROOT, reason="Set EP_PLATFORM_LIVE_ARCHIVE_ROOT to run against the real archive"
)

FORM = """Materials Submittal Form
Project Name:
BINGHATTI SKYBLADE
Main Contractor:
Granada Europe Const. Co.- MEP
MAS Reference No.
{reference}
MAS
Date:
20 April 2026
MAS Rev.:
{revision}
MAS SUBMITTALS INFORMATION:
{title}
381
EL
M/S. ALARABIA FOR SAFETY AND SECURITY LLC. /
M/s. {supplier}
MAIN CONTRACTOR CONFIRMATION
ENGINEERING COSULTANT COMMENTS AND APPROVAL STATUS
{boxes}
{reply}"""

BLANK_BOXES = "☐ Approved (A)\n☐ Approved as Noted (B)\n☐ Re- Submit (C)"


def _form(
    path: Path,
    reference="BBY006-GME-MAS-EL-FA-0001",
    revision="00",
    title="Material Submittal for Fire Alarm, Fire Telephone & Voice Evacuation System",
    supplier="EDWARDS",
    boxes=BLANK_BOXES,
    reply="",
    pages=1,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page()
    text = FORM.format(reference=reference, revision=revision, title=title, supplier=supplier, boxes=boxes, reply=reply)
    page.insert_text((40, 50), text, fontsize=7)
    for _ in range(pages - 1):
        doc.new_page().insert_text((40, 50), "Catalogue page", fontsize=9)
    doc.save(path)
    doc.close()
    return path


# --- the reply code ------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("(B) Approved As Noted", "B"),
        ("11. Samples for all items to be submitted for final approval. (B) Approved As Noted Ce", "B"),
        ("(C) Revise & Resubmit", "C"),
        ("(C) Re- Submit", "C"),
        ("Approved (A)  ", None),  # the blank checkbox row, one code per line
        ("(A) Approved", "A"),
        ("CO Approved (A) approved as Noted (B) Re- Submit (C)", None),  # the row, as OCR reads it
        ("Subject to (A) approval of the authority", None),
        ("MEP (B): Technically no objection.", None),
    ],
)
def test_read_reply_code(line, code):
    assert read_reply_code(f"header\n{BLANK_BOXES}\n{line}\nfooter")[0] == code


def test_a_ticked_checkbox_is_read_too():
    ticked = "☐ Approved (A)\n☒ Approved as Noted (B)\n☐ Re- Submit (C)"
    code, line = read_reply_code(f"APPROVAL STATUS\n{ticked}")
    assert (code, line) == ("B", "☒ Approved as Noted (B)")
    # The tick and its label split across lines still count.
    assert read_reply_code("APPROVAL STATUS\n☒\nApproved as Noted (B)") == ("B", "Ticked on the form (B)")
    assert read_reply_code("☒ Re- Submit (C)")[0] == "C"


# --- scanning a folder ----------------------------------------------------------


def test_a_form_with_a_reply_is_approved(tmp_path):
    _form(tmp_path / "03- MS" / "01- FA" / "form.pdf", reply="(B) Approved As Noted")
    (forms, warnings) = scan_folder(tmp_path, use_ocr=False)
    (form,) = forms
    assert (form.reference, form.revision, form.reply_code, form.status) == (
        "BBY006-GME-MAS-EL-FA-0001", "R00", "B", "approved",
    )
    assert (form.system_code, form.supplier, form.submitted) == ("FAS", "EDWARDS", "20 April 2026")
    assert form.title.startswith("Material Submittal for Fire Alarm")
    assert warnings == []


def test_a_form_without_a_reply_is_under_review(tmp_path):
    _form(tmp_path / "03- MS" / "01- FA" / "form.pdf")
    (form,) = scan_folder(tmp_path, use_ocr=False)[0]
    assert (form.reply_code, form.status) == (None, "under_review")


def test_resubmit_is_not_an_approval(tmp_path):
    _form(tmp_path / "03- MS" / "03- FRC" / "form.pdf", reference="BBY006-GME-MAS-EL-FA-0004", supplier="TIANJIE",
          reply="(C) Revise & Resubmit")
    (form,) = scan_folder(tmp_path, use_ocr=False)[0]
    assert (form.reply_code, form.status, form.system_code, form.supplier) == ("C", "rejected", "FRC", "TIANJIE")


def test_the_copy_that_came_back_is_the_one_kept(tmp_path):
    """The same submittal is filed where it was prepared and again under the
    approval folder; only the returned copy carries the consultant's reply."""
    _form(tmp_path / "03- MS" / "01- FA" / "form.pdf")
    _form(tmp_path / "08- approval" / "MS" / "FA" / "form.pdf", reply="(B) Approved As Noted")
    forms, _ = scan_folder(tmp_path, use_ocr=False)
    assert len(forms) == 1
    assert forms[0].reply_code == "B"
    assert forms[0].relative.startswith("08- approval")


def test_revisions_are_separate_submittals_and_forms_are_found_inside_packages(tmp_path):
    _form(tmp_path / "03- MS" / "01- FA" / "r0.pdf", revision="00")
    _form(tmp_path / "03- MS" / "01- FA" / "r1.pdf", revision="01", reply="(A) Approved", pages=40)
    forms, _ = scan_folder(tmp_path, use_ocr=False)
    assert [(f.revision, f.status) for f in forms] == [("R00", "under_review"), ("R01", "approved")]


def test_other_documents_are_not_submittals(tmp_path):
    # A sample approval request quotes the submittal's reference but is not one.
    sample = tmp_path / "08- approval" / "samples" / "sample.pdf"
    sample.parent.mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page().insert_text(
        (40, 50), "Sample Approval Request\nSAR Reference No.\nBBY006-GME-SAR-EL-FA-0001\nrelated: BBY006-GME-MAS-EL-FA-0001\n(B) Approved as Noted", fontsize=8
    )
    doc.save(sample)
    doc.close()
    (tmp_path / "notes.pdf").write_bytes(b"not a pdf")
    assert scan_folder(tmp_path, use_ocr=False)[0] == []


@requires_live_archive
def test_live_ep30784_submittals():
    folder = Path(LIVE_ROOT) / "Granada Europe/EP-30784 - Binghatti Skyblade"
    forms, warnings = scan_folder(folder)
    assert warnings == []
    assert [(f.reference, f.revision, f.reply_code, f.status, f.system_code, f.supplier) for f in forms] == [
        ("BBY006-GME-MAS-EL-FA-0001", "R00", "B", "approved", "FAS", "EDWARDS"),
        ("BBY006-GME-MAS-EL-FA-0002", "R01", "B", "approved", "FRC", "FIREGUARD"),
        ("BBY006-GME-MAS-EL-FA-0004", "R00", "C", "rejected", "FRC", "TIANJIE"),
        ("BBY006-GME-MAS-EL-LI-0001", "R00", "B", "approved", "EML", "EATON"),
    ]
    # Every reply came from the consultant's stamp, not the form's text.
    assert all(f.ocr_used for f in forms)
