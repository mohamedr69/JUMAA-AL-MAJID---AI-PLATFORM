# Compliance statements: find, verify, prepare, check

Code: `backend/app/compliance/`, `backend/app/services/spec_finder.py`,
`backend/app/routers/compliance.py`, `frontend/src/pages/ProjectCompliancePage.tsx`.
Tests: `backend/tests/test_compliance_statements.py`, `test_spec_finder.py`, `test_compliance.py`.

The rule throughout: Python reads, matches, writes and counts; the model is
asked only what Python cannot settle, many clauses per call, cached.

## 1. Finding the specification

Audit of the 13 projects in the database (13 Sep 2026):

| Finding | Fix |
|---|---|
| Six projects (EP-29429, 30208, 30387, 30977, 31055, 31725) hold **no specification file at all** -- drawings, SOMs, warranties, design sheets only. | Nothing to detect; the page offers the draft mail. |
| EP-30353's only copy of its specification is **inside its past PA/BGM material submittal** (section 27 51 16, pages 54-67). | For a system with nothing else found, up to 8 submittal PDFs are opened and the section's pages located (`matched_on = "submittal"`). |
| Real sections the finder did not know: **28 46 00** (FAS, MasterFormat 2012), **26 52 13** (EML), **27 51 23/26** (PA), and 1995 five-digit numbers (16721). | Added. |
| A BGM/voice evacuation specification was offered as **Fire Alarm** because FAS listed "voice evacuation" as a keyword. | Removed; a combined "fire detection and voice evacuation" section still reads as FAS by "fire detection". |
| Folder hint "back doc" let **schematic drawings** through (their notes say "GENERAL"). | Drawings excluded by name; a one-page document needs a SECTION heading. |
| EP-30784's 283111 and 265200 are headed **"BUGATTI RESIDENCE ON PLOT 3466814"**; the project is on plot 3450398. The finder offered them as the project's. | Every specification found is verified (next section). |
| EP-29495's `MS\FA\FA SPECS.pdf` and `MS\CBS\CBS SPECS.pdf` open "( I ) FIRE DETECTION SYSTEM / PART 1 - GENERAL" and **never carry a section number**; nothing matched. | A document that reads like a specification and names the system in its opening lines is that system's (`matched_on = "content"`). A last pass opens the first page of up to 150 PDFs named for nothing, for systems still missing. |
| An **uploaded** specification vanished: its stored name ("Specification FAS <stamp>.pdf") said nothing the finder recognised and the content had no section number. | Uploads are listed for the system the engineer chose, without detection (`_uploaded_specs`); one that does not read like a specification is shown as "Uploaded document" and can be removed. |
| A system added under **Project Info** (EP-29495's Central Battery System) got no tab: tabs came from Design Sheets/BOQ only, Project Info being a fallback. | Tabs are the union of Project Info, Design Sheets, BOQ and uploads; a Design Sheet's `ELS` reads as CBS when Project Info marks Central Battery System, else EML. |

## 2. Is it this project's specification? (`verify.py`)

From the running header (lines repeated on half the pages) and the cover:

- EP number named -> same; project's plot named -> same; another plot named -> **different**;
- the project's distinctive name words (generic words like "residential", "tower" ignored) -> same;
- otherwise **unknown**. Only unknown is worth the model: *Verify with AI* on the page (`verify_spec`, one call per document, cached).

System: section number or title words against `SYSTEMS`.

## 3. Prepare (`service.prepare`)

1. `spec_text.read_document` rebuilds the outline -- PART / 1.2 / A / 1 / a / i -- from labels, accepting a label only when it is next in sequence (so "10.5 mm" or "U.S." never opens a clause). Table-of-contents pages are skipped; header and footer removed.
2. Per clause, first answer wins:
   - heading -> not answered; lead-in ("Include the following:") -> answered by its items;
   - rule -> **Noted** (related documents, definitions, references, standards, related sections);
   - past statement, similarity >= `COMPLIANCE_REUSE_SIMILARITY` (0.86), answer Comply/Noted, remark naming no other manufacturer -> **reused**;
   - everything else -> model, in batches, with the nearest past answer as a hint and the project's facts and BOQ; project-dependent past answers (Not applicable, By others, Deviation) are always re-asked.
   - no model / model failed -> nearest past answer marked *review*, or blank for the engineer.
3. The engineer edits in the table (source becomes *Engineer*); **Export Excel** writes the company layout (title block, SL.NO | SPECIFICATION | COMPANY COMPLIANCE | CONTRACTOR COMPLIANCE | REMARKS).

Measured on EP-30784's 283111 (21 pages, 393 clauses read, 358 answerable), with 171 past statements indexed:

| | Clauses |
|---|---|
| Past statements (274 word for word from EP-20779) | 323 |
| Rules | 15 |
| Model | 20, in **one call** (2,624 tokens in / 417 out, 7 s total) |

### The page (`ProjectCompliancePage.tsx`)

One tab per system with a status pill (Draft / Spec found / Wrong project / No spec). A
specification bar names the document in use, its verification, and the others found
("Use this one"). The clauses card is the statement itself: **Start statement** reads the
clauses and fills what rules and past statements settle (no model call); each row has a
response dropdown and a remark, auto-saved 1.2 s after typing ("All changes saved");
a progress bar counts met / with remark / deviation; **Export Excel** writes the workbook.
The assistant panel: sources in use, a fill scope (unanswered / to review / all) and
**Auto-fill with AI** (`POST .../autofill`), a **Suggested response** for the clicked clause
(`POST .../suggest`, applied only on "Apply suggestion"), and **Ask AI about a clause**
(`POST .../ask`, a free question answered from the project facts and BOQ). Checking an
existing statement is a collapsed section of the same panel.

## 4. The past statements (`references.py`, `statements.py`)

`PROJECTS_ROOT` holds ~2,900 files named as compliance statements; ~870 are
.xlsx/.xls/.docx and readable as tables (the PDFs are not used). Columns are
found by content -- the answer column is the one full of answers -- and merged
answer cells are filled down. The walk runs in the background (page button
*Update*, or `python scripts/index_compliance_references.py`), stores the index
in `CACHE_ROOT/compliance-references/index.json.gz`, and re-reads only files
whose size or time changed. First walk on the synced drive: ~4 s per file.

## 5. Check (`service.check`)

A statement (uploaded, or chosen from the project folder) against the specification:

- deterministic: specification of another project/system; statement header naming another project or manufacturer; clause **missing**; clause **unanswered**; clause worded differently; rows not in the specification;
- model (`review_clauses`): answers to project-dependent or hard-requirement clauses (values, listings, brands) against the BOQ -- at most 60, batched. *Conflict* is flagged; *unclear* is shown as info only (it was mostly noise).

## 6. Provider limits

The model is Claude, reached through the Claude Code CLI on the Claude
subscription signed in on the server (`AI_PROVIDER=claude-code`, no API key).
Each call starts `claude -p` once and takes roughly 10-30 seconds; there is no
per-minute token pacing. Calls count against the subscription's usage limits,
and a limit reached is reported as `rate_limit` rather than retried. The machine
running the backend must stay signed in to Claude Code.

## Open

- Prepare and check run inside the HTTP request; with pacing a long
  specification can take minutes. A background job with progress would be better.
- Scanned (image-only) specifications are reported, not OCR'd.
- Word statements whose clauses sit outside tables are not read.
- Model answers are proposals; quality has been spot-checked on one project only.
