"""Document Processing V2: the reading pipeline measured and made cheaper
without reading less -- one PDF open per document, page text extracted
once, OCR only where the evidence calls for it, and the timing of every
stage on the result."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.services import document_control, document_processing, document_sync

DRAWING_TEXT = ("Drawing title\nBBY006-GME-SDW-EL-FA-0001\nREV. 01\nGround Floor Layout\n"
                "FIRE ALARM LAYOUT -- GROUND FLOOR PLAN, SCALE 1:100, SHEET 1 OF 1, ISSUED FOR APPROVAL 2026-09-01")


def _pdf(path: Path, pages: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((40, 50), text, fontsize=8)
    doc.save(path)
    doc.close()
    return path


def _read(path: Path, relative: str = "05- Drawings/L01.pdf") -> dict:
    return document_processing.read_task(str(path), relative, None, True)


# --- phase 0: every stage is measured --------------------------------------------------------


def test_a_reading_reports_where_its_time_went(tmp_path):
    result = _read(_pdf(tmp_path / "L01.pdf", [DRAWING_TEXT]))
    timing = result["timing"]
    assert set(timing) >= {"storage_wait_ms", "hash_ms", "total_ms", "stages_ms", "counts"}
    stages = timing["stages_ms"]
    for stage in ("classification", "pdf_open", "pdf_text_extract", "deterministic_extract"):
        assert stage in stages, stage
    counts = timing["counts"]
    assert counts["page_count"] == 1 and counts["pages_scanned"] == 1 and counts["read_pdf"] == 1
    assert result["role"] == "document" and result["records"][0].reference == "BBY006-GME-SDW-EL-FA-0001"
    assert result["seconds"] >= timing["total_ms"] / 1000


def test_the_telemetry_aggregates_stages_percentiles_and_shares():
    telemetry = document_sync.Telemetry()
    for n, ms in enumerate((100, 200, 300, 4000)):
        telemetry.document(path=f"{n}.pdf", seconds=ms / 1000, role="document", result="processed", size=(n + 1) * 1_000_000,
                           timing={"stages_ms": {"pdf_open": 10, "ocr_engine": ms / 2}, "counts": {"pdf_opens": 1,
                                   "pages_scanned": 2, "ocr_pages_attempted": 1 if ms > 250 else 0}, "hash_ms": 5,
                                   "db_write_ms": 1})
    out = telemetry.result()
    assert out["documents"] == 4 and out["percentiles_ms"]["median"] == 300 and out["percentiles_ms"]["p99"] == 4000
    assert out["stage_ms"]["pdf_open"] == 40 and out["stage_ms"]["hash"] == 20 and out["stage_ms"]["db_write"] == 4
    assert abs(sum(out["stage_share_pct"].values()) - 100) < 0.5
    assert out["events"] == {"ocr_pages_attempted": 2, "pages_scanned": 8, "pdf_opens": 4}
    assert out["ocr_documents"] == 2 and out["largest_files"][0]["path"] == "3.pdf"
    assert out["processor_version"] == document_sync.PROCESSOR_VERSION


# --- phase 1: no duplicate work -----------------------------------------------------------------


def test_a_document_is_opened_once_and_its_first_page_text_extracted_once(tmp_path):
    """Classification and extraction share one open PDF and one text
    extraction of page 1, instead of opening the file twice and reading
    the same page twice."""
    result = _read(_pdf(tmp_path / "L01.pdf", [DRAWING_TEXT, "Notes page\nGeneral notes for the layout"]))
    counts = result["timing"]["counts"]
    assert counts["pdf_opens"] == 1, counts
    assert counts["page_text_extractions"] == counts["pages_scanned"], counts
    assert result["records"][0].reference == "BBY006-GME-SDW-EL-FA-0001" and result["records"][0].revision == "R1"


def test_a_form_is_classified_and_read_from_the_same_open_document(tmp_path):
    from .test_submittal import _submittal_form

    form = _submittal_form(tmp_path / "03- MS" / "01- FA" / "form.pdf")
    result = _read(form, "03- MS/01- FA/form.pdf")
    assert result["role"] == "submittal_form"
    assert result["timing"]["counts"]["pdf_opens"] == 1
    assert result["records"][0].reference == "BBY006-GME-MAS-EL-FA-0001"


def test_an_unreadable_file_is_still_a_document_with_the_reason_noted(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not a pdf at all")
    result = _read(bad, "broken.pdf")
    assert result["role"] == "document" and result["records"] == ()
    assert any(note.startswith(document_control.UNREADABLE) for note in result["notes"])


# --- OCR is evidence-driven -------------------------------------------------------------------------


def test_a_text_pdf_with_its_evidence_on_the_page_is_not_ocred(tmp_path, monkeypatch):
    monkeypatch.setattr(document_control, "_ocr_images", lambda page, images: pytest.fail("OCR ran on a text page"))
    result = _read(_pdf(tmp_path / "L01.pdf", [DRAWING_TEXT]))
    assert result["timing"]["counts"].get("ocr_pages_attempted", 0) == 0
    assert result["records"][0].revision == "R1"


def _png_with_text(text: str, size=(420, 120), font_size: int = 34) -> bytes:
    import io

    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=font_size)
    except TypeError:  # an older Pillow: the bitmap default
        font = ImageFont.load_default()
    draw.text((12, 30), text, fill=0, font=font)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _pdf_with_image(path: Path, text: str, png: bytes, rect=(400, 60, 560, 110), *, text_layer: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page()
    if text_layer:
        page.insert_text((40, 50), text, fontsize=8)
    page.insert_image(pymupdf.Rect(*rect), stream=png)
    doc.save(path)
    doc.close()
    return path


needs_tesseract = pytest.mark.skipif(not document_control.ocr_available(), reason="Tesseract is not installed")


def test_a_text_page_with_a_small_image_is_ocred_by_region_not_whole_page(tmp_path):
    """A CAD sheet carries a logo on every page; a logo is not a stamp, and
    the page's words are in its text layer. Only the image is OCRed."""
    result = _read(_pdf_with_image(tmp_path / "L01.pdf", DRAWING_TEXT, _png_with_text("LOGO", (120, 60))))
    counts = result["timing"]["counts"]
    assert counts.get("ocr_regions", 0) >= 1, counts
    assert counts.get("ocr_full_pages", 0) == 0, counts
    assert result["records"][0].reference == "BBY006-GME-SDW-EL-FA-0001" and result["records"][0].status == "UR"


@needs_tesseract
def test_a_consultant_stamp_pasted_as_an_image_is_still_read(tmp_path):
    """The stamp is an image on a text page: region OCR reads it, and the
    decision it carries stands, exactly as the full-page OCR found it."""
    stamp = _png_with_text("Status: (A) APPROVED", (520, 110), 30)
    result = _read(_pdf_with_image(tmp_path / "L01.pdf", DRAWING_TEXT, stamp, rect=(300, 400, 560, 455)))
    assert result["records"][0].status == "approved", result["records"]
    assert result["timing"]["counts"].get("ocr_full_pages", 0) == 0


@needs_tesseract
def test_a_scanned_page_still_gets_the_full_page_ocr(tmp_path):
    """No text layer: the page is a scan, and the whole of it is OCRed as
    before -- the fallback that keeps the accuracy of the old reader."""
    scan = _png_with_text("Materials Submittal Form\nMAS Reference No. BBY006-GME-MAS-EL-FA-0002\nMAS Rev.: 00\n"
                          "Material Submittal for Fire Alarm Sounders", (1400, 700), 40)
    # Under an approval folder: a scan with no words is OCRed only where its
    # name or folder says it could be a submission (the reader's own rule).
    path = _pdf_with_image(tmp_path / "08- approval" / "scan.pdf", "", scan, rect=(20, 20, 580, 300), text_layer=False)
    result = _read(path, "08- approval/scan.pdf")
    counts = result["timing"]["counts"]
    assert counts.get("ocr_full_pages", 0) == 1, counts
    # Tesseract reads a printed 0 as O now and then (the reader's known
    # limitation, not what is tested here): the reference is compared with
    # the two made the same.
    assert result["records"], result["notes"]
    assert result["records"][0].reference.replace("O", "0") == "BBY006-GME-MAS-EL-FA-0002"
    assert result["records"][0].category == "submittals"


@needs_tesseract
def test_the_same_content_read_again_comes_from_the_ocr_cache(tmp_path):
    scan = _png_with_text("Materials Submittal Form\nMAS Reference No. BBY006-GME-MAS-EL-FA-0003\nMAS Rev.: 00",
                          (1400, 500), 40)
    path = _pdf_with_image(tmp_path / "08- approval" / "scan.pdf", "", scan, rect=(20, 20, 580, 240), text_layer=False)
    first = _read(path, "08- approval/scan.pdf")
    again = _read(path, "08- approval/scan.pdf")
    assert first["records"] == again["records"]
    assert again["timing"]["counts"].get("ocr_cache_hits", 0) >= 1
    assert again["timing"]["stages_ms"].get("ocr_engine", 0) == 0


def test_a_second_copy_of_the_same_content_reuses_the_reading(tmp_path):
    """ABC-R0.pdf and Archive/ABC-R0.pdf with the same bytes: the second is
    its own file (its own row, its own path) but is not read again."""
    first = _pdf(tmp_path / "05- Drawings" / "L01.pdf", [DRAWING_TEXT])
    copy = tmp_path / "Archive" / "L01.pdf"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(first.read_bytes())
    original = _read(first)
    duplicate = document_processing.read_task(str(copy), "Archive/L01.pdf", None, True,
                                              known_shas=frozenset({original["sha"]}))
    assert duplicate.get("duplicate_of") == original["sha"]
    assert "records" not in duplicate and duplicate["timing"]["counts"].get("pdf_opens", 0) == 0
