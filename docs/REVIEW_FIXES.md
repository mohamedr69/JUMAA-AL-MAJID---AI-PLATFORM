# Ten-project review: finding-by-finding record

Companion to `EP-Platform-Review-English.pdf` and the remediation brief of
13 September 2026. For every FX item: what was reproduced, the cause found
in the code, what changed, how it was tested, and what is not verified.

The reproductions used the ten projects the review left in the platform
(ids 4-13) and their source documents in the archive. No archive document
was modified. Existing projects were not rewritten; `scripts/repair_projects.py`
proposes the data repairs and applies them only with `--apply`.

Regression tests for every item are in `backend/tests/test_review_fixes.py`.

---

## FX-01 Source selection -- fixed, verified

**Reproduced.** Project 7 (EP-31112) is stored with `source_folder_path` under
`AG Engineering\...\EP-31112 Commerical documents` and `drf_document_path` under
`Al Ghurair MEP\EP-31112 - MEAISEM GOLF Substation\...`.

**Cause.** `frontend/src/pages/CreateProjectPage.tsx` computed the source
folder as `matched_folders[0]` -- the first match -- regardless of the folder
the engineer selected. The resolve response did not say which folder it had
read the documents from. The create endpoint accepted any paths.

**Change.**
- `app/services/ep_resolver.py`: `ProjectResolution.source_folder` is the
  folder the documents were found in (the one match, or the selected one).
- `app/routers/projects.py`: the resolve response carries `source_folder`;
  `create_project` refuses a source folder outside the archive and a DRF or
  design sheet outside the source folder (`_validate_source_paths`, active
  only where `PROJECTS_ROOT` is configured).
- `CreateProjectPage.tsx` / `ReviewProjectForm.tsx` save `source_folder`.
- DRF candidates are ranked: PDF before image, original before "Copy",
  shortest name first. EP-30208's JPG copy is the top half of the page only
  and was being read first.

**Tests.** `test_fx01_*` (4 tests): resolution names the selected folder;
the endpoint returns it after selection; a mismatched DRF is refused with
422 and a consistent one accepted; the DRF ranking.

**Data.** `repair_projects.py` proposes the DRF's own EP folder for project 7.

**Not verified.** The browser flow end to end (the form now reads the field
the endpoint returns; the types are checked by `tsc`).

## FX-02 DRF fidelity -- fixed, verified on the real forms

**Reproduced** by running the extractor on the stored DRFs:
- Other Information: not extracted on any form (`other_information` did not
  exist in the extractor).
- EP-30208 (JPG): "Could not locate the Systems table" -- the JPG is a scan of
  the top half only. The PDF beside it reads `PA/VA & BGM` with MS/DWG ticks.
- EP-30387: PA/VA & BGM row unmarked. The brand cell holds 638 dark pixels;
  the threshold calibrated on "EDWARDS" is 1000. OCR of that cell reads
  "TOA" at confidence 96.
- EP-31725: plot number blank. The block read (psm 6) returns nothing for
  "2320316"; the sparse read (psm 11) returns it at confidence 54.
- EP-31112: plot "ee ee ee" at 24.7, consultant "ee ee" at 26.5.

**Change** (`app/services/drf_extractor.py`):
- `_read_other_information`: finds the "OTHER INFORMATION" banner (inverted
  OCR of the navy band), reads the box to the rule under it, keeps lines that
  are writing and drops signature scribble and border fragments.
- Brand cells between 400 and 1000 dark pixels are read and kept only at OCR
  confidence >= 70 and not noise.
- `_read_value`: sparse-text fallback on an empty block read, keeping tokens
  of three characters or a digit.
- `_is_ocr_noise`: a read under confidence 45 made only of one- and
  two-letter fragments is blank.

**Result on the real forms** (after): EP-30208 PDF -> PA/VA & BGM TOA MS+DWG,
plot "KALBA-1117, DHAID-1298", notes read; EP-30387 -> TOA, notes read
("Document Attached along with DRF: / 1. Design Sheet. / 2. LPO / 3. Testing
and Commissioning Support Only"); EP-31725 -> plot 2320316, notes "1. Quoted
as per BOQ and Evac Only Considered. / 2. The request for T&C Only."; EP-31112
-> plot and consultant blank (were noise), notes "1.Fire alarm system brand
will be advised later."; EP-31055 -> "1. Quoted as per BOQ only / 2. Our
scope is device fixing only, Cables are excluded."

**Tests.** `test_fx02_*` (unit tests on the noise, prose and cleaning rules).
The live DRF checks above are reproducible with `EP_PLATFORM_LIVE_ARCHIVE_ROOT`.

**Not done.** Source snippets beside extracted fields; a correction audit;
distinguishing "blank / unknown / not requested" as three states. The form
still shows confidence per field only.

## FX-03 BOQ extraction -- fixed, 92/97 rows on the fixture

**Reproduced.** EP-30208 PA Design Sheet: 4 scanned pages, 50 lines / 232
quantity extracted.

**Cause.** `design_sheet_extractor._table_extent` took the *longest
continuous run* of the column rule on each page as "the table". The sheet is
nine separate ruled boxes (one per building) closed by full-width totals rows
and building banners across which the rules do not run; only the longest box
per page was read.

**Change.** `_table_extents` returns every rule run of table height; every
box on a page is read in order. The band above each box is read for a
capitalised banner ("DHAID - B2 BUILDING"), which becomes the section of
every line's group heading (`"DHAID - B2 BUILDING / f. Speakers"`). A section
carries across a page break. A capitalised first heading inside a box
followed by a sub-heading is treated as the banner (page 1's first building
prints its banner inside the ruled box). A heading must contain letters
(border artefacts "|" and "~" were becoming group headings).

**Result.** 92 lines / 701 quantity, all nine buildings present: Dhaid B1 13
rows, B2 14, B3 13, B4 12, B5 12; Kalba B2 1, B4 1, B5 14, B6 12. The 74
and 214 ceiling-speaker rows are present under their buildings. Source is 97
/ 700; the five missing rows and the one-unit surplus are per-row OCR
quantity misreads (a row whose quantity cannot be read is dropped, as
before). Banner spellings follow OCR ("DH AID - BS BUILDING" for B5).

**Tests.** `test_fx03_every_building_box_is_read_and_kept_apart` (synthetic
three-box sheet; requires Tesseract). Existing extractor tests unchanged.

**Not done.** Per-row page/region provenance in the database (the line keeps
its page number only); a reviewer reconciliation view; typed quantities with
units.

## FX-04 System and revision lineage -- fixed for new projects; data repair proposed

**Reproduced.** Sheets named PA / VA / VAS / VE stored with `system_code`
None (projects 4, 5, 6, 8, 9, 10, 11, 13); their BOQ lines "Unassigned" with
no manufacturer; Compliance and the submittal builder had no system for them
because `SYSTEM_DRF_ROWS`, `spec_finder.SYSTEMS`, `SYSTEM_TITLES` and
`document_control.SYSTEMS` did not know PAVA. EP-30175 had FAS (2 sheets) and
VE/VES (3 sheets) all attached.

**Change.**
- `ep_resolver.canonical_system_code`: PA/VA/VAS -> PAVA, VE -> VES; applied
  on resolve, on create and on upload.
- `infer_single_system`: a sheet with no code takes the DRF's one marked
  system when the DRF marks exactly one; otherwise it stays unlabelled and
  the review form says its lines will be Unassigned.
- `declared_revision` / `mark_superseded`: within a system, the highest
  declared revision is selected by default; the rest are offered unticked
  with "superseded by ...". The review form shows every sheet with a
  checkbox and a system select.
- PAVA added to Compliance, the specification finder (sections 275116,
  275100, 274116, 275113), the submittal titles, the document log and the
  drawing finder.
- `_system_title`: an FAS cover names only Fire Alarm / Voice Evacuation /
  Fire Telephone and never "System System".

**Tests.** `test_fx04_*` (8 tests).

**Data.** `repair_projects.py --apply` would set: project 4 sheet -> PAVA and
its 50 lines -> PAVA / TOA; project 5 sheet -> PAVA (DRF marks PAVA only)
and 11 lines -> PAVA / "TOA (PA)"; project 6 VE sheets -> VES; project 9 and
11 sheets -> PAVA and VES; project 7 source folder. It leaves for the
engineer: project 6 and 8 unassigned lines (two systems' sheets, cannot be
told apart after the fact), projects 10 and 13 generic sheets (DRF marks two
systems). Nothing has been applied.

**Not done.** Document content hashes; a stored "source selection" entity;
staleness marking of calculations when a source changes; row-to-panel
traceability beyond the group heading.

## FX-05 Battery scope and status -- partly fixed

**Preserved.** The arithmetic is untouched: (8.38376 x 24 + 14.76776 x 0.5) x
1.2 = 250.312944 Ah is what `battery_calculation.py` computes; the existing
tests pin it. Amplifier logic untouched (the tab is marked "soon" on the
owner's instruction; the API and tests remain).

**Change.** `battery_pdf.panel_manufacturer`: the panel's brand from its own
BOQ lines, else the DRF's brand for its system, else the one brand the DRF
names; otherwise "not established". `battery_systems_title`: the Submittal
line names the panels' systems, not every project system. Applied by the
export endpoint and the submittal's battery section.

**Result on EP-30175 FACP-01.** Manufacturer "not established" (DRF: Fire
Alarm COOPER, Voice Evacuation EDWARDS; BOQ lines carry no brand), which is
the truthful state the review asked for; the header no longer says "COOPER /
EST4".

**Tests.** `test_fx05_the_sheet_names_the_panel_s_own_brand_or_none`.

**Not done.** Per-current unit / operating mode / review status fields;
charger, cabinet and mixed-capacity checks; binding a calculation to an
accepted BOQ version. The repeated CPU rows on EP-30175 are the overlapping
source revisions (FX-04) -- the governing-revision default prevents them on
new projects; existing lines need the BOQ page.

## FX-06 Battery PDF -- fixed, verified

**Reproduced.** EP-30175 FACP-01: 60 load rows on one A3 page; rows past
~20 drawn below the page edge (text present, not visible).

**Change.** `battery_pdf._panel_page` holds 11 rows beside the sizing card
and notes on the first sheet and continues the table on further sheets (25
rows each) with the header repeated and "(continued, sheet 2 of 3)". The
first sheet says how many parts follow. `_status_lines` prints the screen's
warnings beside the selection: lower-bound load with the count of parts
without a current, no selection, and "Charger compatibility: not checked".

**Result.** FACP-01 renders as 3 pages; all 60 part numbers present in the
text; every row above the footer. Pages rendered and inspected.

**Tests.** `test_fx06_*` (2 tests: 60-part panel across 3 pages with every
part and both notes present, rows above the footer; the warnings text).

**Not done.** Input/rule version identifiers on the sheet; removal of the
template's fixed installation notes (they are the company's sheet text and
were left as the owner set them).

## FX-07 Submittal assembly -- partly fixed; original failure not reproduced

**Reproduced.** "? pages assembled": the response header `X-Package-Pages`
was set but not in the CORS `expose_headers`, so the browser could not read
it. Cover scope: `_system_title` listed every non-"emergency" DRF row and
appended " System" to "Central Battery System". Divider: the template's
printed "PAGE 07" was not rewritten.

**Not reproduced.** The rich build (Schedule + Technical Data Sheet) on
EP-29495 builds directly: 177 pages, 4 datasheets reported missing (FireKey,
Compaq, 3-CABSB, SIGA-AASO), no error. The review's failure happened while
the datasheet library was read off the synced archive over several minutes;
the library now lives locally (`backend/library`), which removes the most
likely cause, but the original failure's cause is not established.

**Change.** Headers exposed; `_system_title` fixed; divider "PAGE nn"
rewritten to the section number; `PackageBuildError` names the stage and
document that failed and the endpoint returns it as the error detail.

**Tests.** `test_fx07_*` (headers exposed; a failing generated section is
named).

**Not done.** A build manifest with included / missing / omitted documents;
atomic build with validation before download.

## FX-08 Specification matching -- not changed (P2)

PAVA sections were added to the finder so PA projects get a tab. Candidate
evidence, explicit acceptance and versioned governing specifications are a
design change not made here. The AI compliance actions remain disabled.

## FX-09 Revision download -- verified working, no change

`GET /projects/13/boq/revisions/0/export.xlsx` on EP-24601 returns 200,
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`,
`Content-Disposition: attachment; filename="EP-24601 BOQ Rev 00.xlsx"`, a
12 KB workbook with sheets Summary and Unassigned, the Summary carrying "Rev
00, issued 13 Sep 2026". The frontend reads the filename from the exposed
`Content-Disposition` header. The review's missing download is recorded as
an observation gap; a browser-level download test was not run.

## FX-10 Jobs and recovery -- session fixed; jobs not changed

**Change.** `app/main.py` `slide_session` middleware: a request made in the
second half of the cookie's life re-issues it for a full term. Login and
logout are untouched.

**Tests.** `test_fx10_the_session_cookie_is_renewed_while_in_use`.

**Not done.** Persisted job ids with queued/running/failed states, progress
for the specification search and library reads, per-file scan outcomes,
idempotent retry. The synchronous compliance search with its 15-minute cache
is as it was.

---

## Test runs

- Backend: `pytest tests` -- see the final run in the delivery message.
- Frontend: `npm run build` (tsc + vite) passes; `npm run lint` reports only
  the pre-existing warnings.
- Live checks (not part of the suite): DRF extraction on projects 4, 7, 8,
  9, 11; EP-30208 design sheet extraction; EP-30175 FACP-01 PDF render;
  EP-29495 rich package build; EP-24601 revision export.

## Data repair

Nothing was applied. `scripts/repair_projects.py` (dry run) proposes 10
changes and lists 4 items that need the engineer; `--apply` makes exactly
the listed changes. Issued revisions are snapshots and are never touched.
