# Engineering Project Platform

Phase 1: authentication, roles/permissions, and the basic app shell.
Phase 2: Create Project / Open Project, EP-number resolution against the
project archive, and DRF extraction (project fields, Scope of Work, and the
Systems table with per-system brand and Method Statement / Drawing ticks).
Phase 3: the project workspace -- Home overview, editable Project Info, and
the BOQ (read once from the Design Sheets, then edited, filtered, totalled,
exported to Excel and issued as frozen revisions).
Phase 4: design calculations -- Voice Evacuation amplifier loading
(imported from the engineer's amplifier workbook, recalculated from the
speaker counts, checked against a load limit held as a design rule), and
panel standby battery sizing and selection from the BOQ.

## Backend (FastAPI)

```
cd backend
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then edit SECRET_KEY (see comment in the file)
.\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Tests: `.\venv\Scripts\python -m pytest tests -v` (auth/RBAC, the EP-folder
resolver, the Project API, DRF and Design Sheet extraction, BOQ export and
revisions, the VE workbook reader, calculation and API, and the migrations). Two kinds of tests are
gated and skip by default:
- DRF-extraction tests need Tesseract OCR (see below) -- skip automatically if it's not installed/configured.
- The live-archive tests (one per real DRF: EP-24601, EP-29495, EP-30784) need `EP_PLATFORM_LIVE_ARCHIVE_ROOT` set to the real project archive path -- they skip otherwise. Never run by default; the rest of the suite uses synthetic fixtures, not the live OneDrive tree. They pin exact extracted values, so they are what catches a regression in the grid-line geometry: those failures are silent otherwise, showing up as a value quietly losing its first word rather than as an error.

Default seeded admin: whatever `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` are set to in `.env` (change the password after first login).

### Database migrations (Alembic)

The schema is managed by Alembic migrations in `backend/alembic/versions/`, and the app runs `alembic upgrade head` itself at startup (`app/migrations.py`), so pulling a change and restarting is enough. `alembic` reads the same `DATABASE_URL` as the app.

To change the schema: edit `app/models.py`, run `.\venv\Scripts\alembic revision --autogenerate -m "what changed"`, read the generated file (autogenerate misses some things and SQLite needs `batch_alter_table`, which `env.py` turns on), then restart. The test suite builds its database through the migrations, and `tests/test_migrations.py` fails if the models and the migrations disagree -- a model change without a migration is caught there, not in the running app.

This replaced `Base.metadata.create_all`, which created missing tables but never altered existing ones: a new column silently did not appear and the app failed at query time (it happened twice). A database created that way before the switch should be marked as already at the baseline with `.\venv\Scripts\alembic stamp 61c335c3ba25` and then upgraded; the dev database has been.

### Project archive + OCR (Phase 2)

- `PROJECTS_ROOT` in `.env`: local path to the synced project archive (dev/test shim -- production should use Microsoft Graph search against SharePoint instead; see `app/services/ep_resolver.py`).
- DRF field extraction needs [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) installed separately (it's a system binary, not a pip package). Set `TESSERACT_CMD` in `.env` to its `tesseract.exe` path if it's not already on `PATH`.
- Design Sheet -> BOQ extraction uses the same Tesseract install as the DRF. No API key or network access is involved.

### The two extractors

Both are deterministic OCR, but they read different shapes and so work differently.

`drf_extractor.py` reads a **fixed form**: rows and columns are both ruled, so it finds the grid lines and OCRs each cell.

`design_sheet_extractor.py` reads a **quotation**. It detects the vertical rules and OCRs each column strip separately -- handing Tesseract the whole table lets it run its own reading order across the columns and interleave the quantity into the description, and handing it one cell at a time is worse again, because it reads a lone digit far better with the rest of the column around it. Rows come from the sheet's own row rules where it has them (which is what keeps a description that wraps onto a second line attached to its quantity), and from text spacing where it doesn't.

There is no single Design Sheet template, so the *number* of vertical rules picks the layout: 6 for the scanned EST4 quotation (`Qty | Catalog No. | Description | Unit Price | Total Price`), 4 for the spreadsheet export (`Catalog | Description | Qty`, quantity on the right). Because a column count alone is a weak signal, an extraction is rejected unless most of its lines carry a numeric quantity -- otherwise a DRF's Systems block, which happens to have six rules, would come back as a BOQ.

Only rows with a quantity are kept -- a BOQ line is something quoted in some amount, and the filter also removes the page furniture (stray header text, totals rows) that survives the earlier checks.

Known limitation: catalog numbers mix `0`/`O` and `1`/`l`/`I`, which are near-identical at the printed size, and the extractor returns what it sees. Quantities are covered exactly by tests. Where a quantity genuinely cannot be read the whole row is dropped, so the extracted count can be short of the printed one -- EP-30784's EML sheet yields 11 of its 12 items for this reason. Prices are never extracted; the Unit/Total Price columns are blank on every sheet in the archive.

Extraction runs **once per project**, by itself, the first time someone with edit rights opens the BOQ tab (`POST /projects/{id}/boq/ensure`); the lines are stored and the BOQ is then an ordinary editable table, split into one tab per system. `projects.boq_extracted_at` records that the read has been attempted and is what makes the call idempotent -- re-reading would either duplicate lines or discard the engineer's corrections, and OCR over a multi-page sheet is slow enough to be felt on every page view. The stamp is set even when a sheet cannot be read, so an unreadable layout is not retried forever. Which sheets failed is stored with it (`projects.boq_extraction_warnings`) and shown on the BOQ page on every visit, not only on the response that did the read -- that response is the one React StrictMode throws away in dev, and since the read cannot be repeated, a lost warning meant a sheet's lines silently missing.

Extraction pre-fills each line's **manufacturer** with the brand the DRF gives that line's system (`FAS` -> the Fire Alarm row, `EML` -> Emergency Light Monitoring, ...). Where a code could be more than one DRF row and they name different brands -- `ELS` can be either emergency-lighting row -- it is left blank rather than guessed. Unit and remarks start empty; the sheets carry neither.

The stamp is *claimed* rather than checked: the sheets are OCR'd first, then a single `UPDATE ... WHERE boq_extracted_at IS NULL` sets it, and only the request whose update hit a row writes the lines. Checking it up front and setting it after the OCR let two overlapping opens both extract and store every line twice -- which React StrictMode does on every first open in dev, since it fires each effect twice. A process-local lock additionally makes the second open wait for the first instead of OCR-ing the same sheets again.

This is a deliberate exception to the "extracted values are suggestions, never auto-submitted" rule that governs the DRF fields -- a BOQ is too long to hand-confirm line by line, so it is written and left editable instead. One consequence worth knowing: because it runs only once, there is no way to re-read a sheet later from the UI.

### Working with the BOQ

The page follows the platform owner's design: the project and revision at the top with Export BOQ and Add Item, then the two **sources** a BOQ can come from -- **As per Design Sheet**, which is what the platform reads and what the table below edits, and **As per IFC Drawings**, which is not built and says so (uploading the issued-for-construction drawings, taking quantities off them and reviewing them against the design-sheet BOQ). Under the source tabs are the totals (items, quantity, systems, estimated cost from the lines' prices), the per-system tabs, and the editable table, paged at 25 lines.

Sections of the design still being built -- Drawings, Technical Queries, Logs, O&M Manual, Reports, Project Team, Settings -- are in the left nav marked "soon" and open a page saying the system is under maintenance. They are listed rather than hidden because the nav is the map of the workspace; what is not built says so instead of pretending.

### Documents

Documents sits straight after Project Info and lists what the resolver matched in the archive: the DRF, the Design Sheets and the project folder. **Whatever it did not find can be uploaded there instead** -- an upload button sits next to each document that is missing, including a named one for every system the DRF marks but no Design Sheet was found for ("No Design Sheet was found for EML, which the DRF marks for this project"), plus one for adding a sheet under any code. A DRF that was found can be replaced; a Design Sheet can be detached from the project, which never deletes the file.

Uploads go to `UPLOADS_ROOT` (a folder per project), not into the archive: the platform reads the project archive and never writes to it. Only PDF and Excel are accepted, since those are what the extractors read, and 60 MB is the limit. Two things an upload deliberately does not do: a DRF's fields are not re-read (they were reviewed at creation and are edited under Project Info), and a Design Sheet added later is not read into the BOQ, because that read happens once per project -- the page says so where it is uploaded.

### Calculation

The design calculations share one section after BOQ, with a tab per system: **Battery** (panel standby batteries, below), **Amplifier** (Voice Evacuation amplifier loading, below) and **Power**, which is marked "soon" and says so. Each tab is its own route (`/projects/{id}/calculations/battery|amplifier|power`), so a calculation can be linked to; the pages' former paths (`/batteries`, `/design/ve`) redirect there.

- **Columns** follow the plan: system (the tab), group, manufacturer, model / part no., description, quantity, unit, remarks -- plus unit and total price, which the Design Sheets have as columns. Prices are blank on every sheet in the archive, so they are kept for the engineer to fill rather than extracted.
- **Filter and totals.** The filter matches part no., description, manufacturer, group, unit and remarks within the current tab. The totals under the table sum the numeric quantities of the lines shown; lines quoted as a word ("Lot") are counted separately rather than guessed at.
- **Possible duplicates** are flagged, never removed: same system, same group heading, same part number and same description (ignoring case, spacing, punctuation). The rule is narrow on purpose. The same part legitimately recurs across a BOQ -- every panel has its own CPU and batteries -- and a part-number-only match flagged 93 of the 279 lines on the three real projects, nearly all correctly listed. This rule flags 3 pairs there, each an item listed twice under one heading. The rule lives in `frontend/src/lib/boq.ts`, since it has to follow unsaved edits.
- **Export** (`GET /projects/{id}/boq/export.xlsx`) writes the *saved* BOQ: a Summary sheet with project details and per-system totals, then one sheet per system in tab order. Numeric quantities are real numbers and the per-sheet totals are live `SUM` formulas, so they follow edits made in Excel. The button is disabled while there are unsaved edits, which would otherwise be missing from the file without any sign.
- **Revisions.** Issuing a revision (BOQ -> Revisions) freezes the saved BOQ as Rev 00, Rev 01, ... with a note, who issued it and when. It is stored as a snapshot, not as references to BOQ rows, because every save replaces those rows wholesale. There is no endpoint that edits or deletes a revision. Issuing is refused when nothing has changed since the last one, which also stops a double-click issuing the same BOQ twice. Any two revisions, or a revision and the current BOQ, can be compared (`GET /projects/{id}/boq/compare?from_rev=0&to_rev=1`): lines are paired on system + group + part no. + description (ignoring case, spacing and punctuation), so replacing a part number reads as one line removed and another added, while a quantity or price edit -- or tidying a line's text, such as removing an OCR artifact -- reads as a change to the same line. Reordering lines alone is not a change. The "nothing has changed" check on issuing uses this same comparison, so the page and the server agree on what can be issued. Each revision exports to Excel on its own.

### Voice Evacuation amplifier loading

A project's VE design (Project -> VE Amplifiers) is imported from the engineer's amplifier calculation workbook, picked from the project's archive folder, and is then an editable schedule: zones with their speaker counts, the tap wattage of each speaker type, the channels (runs of zones on one amplifier output, with its rating) and the racks / APS cabinets that group channels. It is stored as one JSON document per project (`project_designs`, validated by `app/schemas_design.py`). Results are never stored: every read recalculates them (`app/services/ve_calculation.py`), so a figure cannot disagree with the counts it came from.

**The arithmetic is the engineers' own**, read off their workbooks: zone watts = sum of count x tap, a channel's required watts = the plain sum of its zones, a rack's totals = the sums of its channels. Tested against two real workbooks (EP-24601, EP-29495), whose every zone and channel figure it reproduces.

**The pass/fail is not.** No workbook in the archive checks the load against the amplifier -- the rating is typed in by hand and nothing compares the two. The limit applied here -- a channel fails above **80% of its amplifier's rating** (40 W on a 50 W amplifier) -- was set by the platform owner and is stored as a `design_rules` row (`ve.limit` / `amplifier_max_load`), seeded at startup, with its `source`. Note the archive's own "20% spare" convention (EP-17428) is different arithmetic: required x 1.2 <= rating, i.e. ~83%, under which EP-24601's 42 W channel still fails but 41 W passes. Rules are versioned, never edited in place: to change the limit, mark the current row superseded and add the next version. A design copies the fraction (and the rule id) when it is imported, so a later rule change does not silently move a calculation already made; re-importing picks up the rule in force.

**Reading the workbook** (`app/services/ve_workbook_reader.py`) is by headings, not positions, because there is no template: of nine amplifier workbooks sampled from the archive no two share a layout. "Watts per Area" / "Load in watt" (or failing that, two speaker headings) marks the header row; speaker columns are the ones headed as speakers or carrying a tap ("WS-(.5W)"); taps come from a "Tapping Wattage" row or from the heading; a value in "Required Watts" / "Total Wattage" / "AMP W" starts a channel; "Proposed Amplifier" gives its rating ("2 x 50 W" is 100); "Rack" / "APS" groups channels. One headerless layout (EP-29495: models over taps, nothing labelled) is recognised separately and only accepted if its watts column agrees with its counts. The schedule ends at a totals row ("Grand Total") or a note ("Result"). A speaker column that has counts but no tap is refused rather than guessed. 8 of the 9 sampled workbooks read; the ninth (EP-16884) lists one speaker total per circuit with no tap per type, and is refused with a message.

The workbook's own computed figures are imported too, but only to **flag where the sheet disagrees with its counts**. It happens: EP-23315's AMP-2 is merged over four floors but its formula sums two (sheet 38.5 W, counts 53 W), and EP-15792's basement row says 81 W for counts that give 83.5 W. Where the sheet's required watts include spare ("with 30% Spare"), they are not compared. Anything else the reader notices (a rack starting partway through a channel, two ratings on one channel) is stored with the import and shown on every visit.

### Panel standby batteries

Project -> Panel Batteries sizes each fire alarm panel's standby battery from the **saved BOQ** and checks it against the battery the BOQ quotes (`app/services/battery_calculation.py`, `GET /projects/{id}/design/battery`).

**The method is the engineers'**, and three of their workbooks agree on it (EP-20779, EP-30784, and EP-29076's sheet copied into EP-30784): required Ah = (standby mA x 24 h + alarm mA x 30 min) / 1000 x 1.2. The durations, the 1.2 and the 24 V panel voltage are a seeded `design_rules` row (`battery.sizing` / `fas_panel`) with its source; the platform owner confirmed them. EP-20779's three EST3 panels are the test oracle: with the currents the engineer used they come out at the engineer's 60.33 / 32.35 / 26.64 Ah and 65 / 42 / 42 Ah batteries.

**Panels come from BOQ groups.** A group whose heading names a panel ("Fire Alarm Control Panel", "FACP", ...) is one panel type; its heading line (the line with no part number, "... Includes:") gives how many identical panels it quotes, and the lines under it are **per panel** -- the sub-panel groups quote one chassis and one backbox for two panels, which only reads one way. Amplifier / booster power supply groups (APS, BPS) are not sized: they are left out on purpose and listed as such. Every BOQ group is shown on the page with what was done with it, so a panel whose heading does not match is visible as "not calculated" rather than missing.

**Currents come only from datasheets.** A part's standby and alarm mA is a `design_rules` entry (`part.current`, keyed by part number) that an engineer enters from the datasheet, with the datasheet named as its source -- nothing is seeded, and a value without a source is refused. Mechanical parts (chassis, filler plates, cabinets, doors) are recorded once as "no electrical load". Batteries are recognised from the battery catalogue (`battery.unit`) or, failing that, from the BOQ's own description ("Battery, 12 V @ 65 AH"), and are not loads.

**A missing current makes the load a lower bound.** Until every part in a panel has a current, the sum is only part of the load: it can show a quoted battery is already too small ("battery too small", shown as "≥ N Ah"), but a panel with a missing part is never reported as sufficient, and no battery is proposed for it. The same holds for a module whose quantity is not a number, and for a panel group that adds up to 0 mA (its modules not itemized -- a lump-sum line, or rows lost to OCR). What the page cannot know is a module the Design Sheet read dropped altogether: check a panel's module list against its schedule.

**Selection** follows the engineers: the smallest battery that covers the requirement on its own; failing that, as many of the largest as fit plus the smallest that covers the rest, as parallel 24 V strings (80.2 Ah from 26 / 42 / 65 -> 65 + 26, as EP-30784's sheet selects). Batteries are selected **from one brand**, held as a `design_rules` row (`battery.selection`: ROCKET, the platform owner's decision), and read from that brand's datasheets in the library: a battery datasheet is recognised by its own heading ("ES 65-12" over "12V - 65Ah", the model having to agree with the rating under it), so dropping ES 26-12 or ES 100-12 into the library is all it takes to widen the choice. Catalogue entries (`battery.unit`) of the same brand join them. Today the library holds ES7-12 and ES65-12, so EP-30784's main panel (110.88 Ah) selects 4 x ROCKET ES65-12 -- two 24 V strings, 130 Ah -- and its sub-panels (42.6 Ah) 2 x ES65-12. No per-charger limit is applied -- if a power supply caps its battery, that is a datasheet value to add.

The battery the **BOQ quotes** is no longer the panel's battery, only a check: `quoted_short` says the quote is smaller than the requirement (EP-30784's main panel quotes 65 Ah against 110.88 Ah), which the page and the export show beside the selection. A panel's status is `ok` (load fully known and a battery selected), `incomplete` (a current missing, a quantity unreadable, or nothing itemized) or `no_selection` (no battery of the brand on file).

**Datasheet libraries.** Each manufacturer's datasheet PDFs live in one folder of the archive, set in `DATASHEET_LIBRARIES` (relative to `PROJECTS_ROOT`). The built-in default is the folder the platform owner designated as the permanent reference for Edwards: `Systems/01- FAVE/01- Edwards - UL&EN/01- EST4`. **Currents are filled from the datasheets automatically.** When an engineer opens Panel Batteries, every part without a current is looked up (`POST /projects/{id}/design/battery/fill-currents`). Its datasheet is found in the library (`app/services/datasheet_library.py`): by filename first ("01- 4-CPU.pdf"), then a datasheet named for its family ("4-NET.pdf" for 4-NET-TP), then any datasheet that mentions it. Its standby and alarm mA are then read off (`app/services/datasheet_currents.py`) and recorded in the catalogue with the datasheet, document number and page as the source. Only parts without a current are touched, so an entry someone corrected is never overwritten.

Datasheet tables are not uniform, so a figure is placed by geometry. Its label is the nearest Standby / Alarm / Current word. Its model is a model named right after it, else the column of a header row naming several models, else the nearest bold section title above it on that half of the page, else the part its datasheet is named for. Headings are told from row labels that merely start with a model by their type (bold, medium or larger). Where a datasheet gives more than one figure, the **worst case** is filled, since a larger figure can only oversize a battery: 4-USBHUB 560 mA fully loaded rather than 44 mA idle; 24L24S 3.0 mA + 0.23 mA × 24 indicators = 8.52 mA; 4-NET-TP 45 mA (CAT5e) rather than 32 mA. Figures per supply voltage are taken at 24 V. The 4-CPU's "Alarm: see the 4-COMREL" takes its standby figure, the relays being their own BOQ line. Figures that belong to another model are never borrowed: 4-2ANN's datasheet gives the 4-ANNCPU's current, and 4-2ANN is left for the engineer. A part with no datasheet current whose BOQ description is mechanical (chassis, filler plate, backbox, cabinet, door, bracket) is recorded as no load. Anything else is listed on the page with the reason and a small entry form. For EP-30784 that is 4-COMREL (no datasheet in the library) and nothing else; its 12 modules read are pinned by live tests.

The library is indexed in the background at startup (about 11 s for the 86 Edwards PDFs) and re-read file by file when files change; manuals over 30 pages are matched by filename only, since they mention every part. **The page (Battery Calculations)** follows the platform owner's design:
- A summary bar: panels, calculated, need input, Export all, Save changes.
- One card per physical panel. A group quoting two identical panels gives two cards (FACP-02, FACP-03), each with its own name, location and settings.
- Per panel:
  - standby and alarm current (A), required capacity and the selected (quoted) battery;
  - **Connected loads**: component, qty, standby / alarm mA per unit, source datasheet, View datasheet (opens the PDF at its page) and Edit;
  - **Calculation settings**: standby duration, alarm duration, design factor, system voltage;
  - the **Battery calculation** working;
  - **Battery selection**: the selected ROCKET battery with its part number and datasheet, the bank (series or parallel strings), and the BOQ's own battery beside it.

Only parts that draw current are listed: mechanical parts, batteries and parts recorded as no load are left out. 4-COMREL is recorded as no load on the platform owner's instruction.

Settings left empty follow the `battery.sizing` rule, and a setting changed on one panel shows the rule's value with a Reset. Edit on a BOQ part corrects the shared catalogue (a new version). Add component adds a load the BOQ does not list, to that panel only, with its source. Names, locations, settings and added components are stored with the project (`PUT /projects/{id}/design/battery`, in `project_designs.document.battery`).

Export all / Export this panel (`GET /projects/{id}/design/battery/export.xlsx[?panel=key]`) writes one sheet per panel in the engineers' layout, with live formulas for the totals, Is, Ia, required Ah and the result. Charger compatibility is not checked yet. Worth knowing when entering values: the datasheets do not always agree with the engineers' old sheets. 24L24S gives 3 mA plus 0.23 mA per lit indicator, where the sheets use 45 mA. 4-AUDTEL's datasheet appears to give 85 mA standby / 101 mA alarm, where the sheets have those two swapped.

**Nothing is stored, deliberately unlike the VE design.** A VE design copies the rule values it was imported with so a later rule change cannot move it. The battery figures are a check of the BOQ as it stands against the catalogue as it stands, recomputed on every read: the BOQ keeps being edited, and a check frozen while its inputs moved on would be worse than none. Each current shown carries the catalogue version it came from. Catalogue entries (`/design-rules/part-currents`, `/design-rules/battery-units`) are shared by all projects and versioned, never edited in place.

### Material submittals

Project -> Material Submittals is the project's submittal register, built to the platform owner's design: the submittal documents, what each covers, which revision went out and where it stands with the consultant (`project_submittals`, `app/routers/submittal.py`).

- **The register**: title, system, manufacturer, revision, status (not submitted / under review / approved / rejected) and when it last changed, with counts across the top and a tab per system.
- **History is kept, not overwritten** (`project_submittal_events`): a submittal rejected before it was approved still says so after the next revision, and the Recent Activity list is that history newest-first.
- **What a submittal covers comes from the BOQ.** Each row carries the number of materials of its system and how many have a datasheet in the manufacturer's library. Quick Actions suggests the submittals the BOQ implies -- one per system with materials, titled for the system ("Fire Alarm System") and pre-filled with the brand the DRF gives it -- and drops a suggestion once the register covers that system.
- **Materials and their datasheets** (`GET /projects/{id}/submittal/materials`) sit under the register: the BOQ's parts, one row per part number per system with quantities summed, each paired with the datasheet found for it (marked "(mentions it)" where the document only mentions the part). On EP-30784, 47 of 52 Fire Alarm materials have a datasheet; the Menvier emergency lighting has none, there being no library configured for that brand.
- **Document Storage** lists the project archive folder's own subfolders with what is in each; a browser cannot open a local folder, so each offers its path to copy.
- **Export** (`GET /projects/{id}/submittals/export.xlsx`) writes the register and the materials-with-datasheets as two sheets.

**Scanning the project folder** (`POST /projects/{id}/submittals/scan`, `app/services/submittal_scanner.py`) fills the register from what is actually filed. A submittal is its form: a first page carrying "MAS Reference No.", the revision, what it covers and, once it comes back, the consultant's approval. The form is a page of a package as often as a file of its own, so every PDF's first page is read; "-MAS-" in the reference is what tells a material submittal from the sample-approval form that quotes it.

**The consultant's reply is a stamp, not text.** The form's own checkboxes ("Approved (A) / Approved as Noted (B) / Re-Submit (C)") stay unticked in the PDF's text layer; the decision arrives as a stamp image. So the page is OCR'd and the stamp read off it. Two traps are guarded: the blank checkbox row lists all three codes at once (a line naming more than one answers nothing), and a bare "Approved (A)" is the form's own label -- only the stamp's order ("(B) Approved As Noted"), a ticked box, or a label with more than the label on its line counts. A form with no reply is **under review**; A and B are approvals (B with comments to take up), C is sent back. Nothing is ever recorded as approved without a reply to read.

The same submittal is filed twice, where it was prepared and again under the approval folder, so forms are grouped by reference and revision and the copy that carries the reply is the one kept. Scanning is idempotent: it creates what is new, updates a status or revision that has changed (both logged to the history) and leaves the rest alone. On EP-30784 it reads four submittals from 287 PDFs in about 40 seconds: FA-0001 R00 (B, Edwards), FA-0002 R01 (B, Fireguard), FA-0004 R00 (C, Tianjie) and LI-0001 R00 (B, Eaton) -- each reply read from the consultant's stamp. Without Tesseract the stamps cannot be read and the scan says so, leaving those submittals under review.

Not built: compiling the submittal package itself (cover, index, merged datasheets), and the design's two AI actions (submittal review, compliance check) -- the page does not show them rather than show buttons that do nothing.

The left nav lists Home, Project Info, BOQ, Panel Batteries, Material Submittal and Documents. VE Amplifiers was taken off it on the platform owner's instruction; the page and its API remain at `/projects/{id}/design/ve`.

## Frontend (React + Vite + Tailwind)

```
cd frontend
npm install
npm run dev
```

Runs at http://localhost:5173, expects the backend at http://localhost:8000 (override with `VITE_API_BASE_URL`).

## Notes

- Roles: Admin, Design Manager, Design Engineer, Draftsman, Viewer.
- Auth uses a JWT in an httpOnly cookie (not localStorage) to reduce XSS token theft. `samesite=lax` assumes frontend and API share a site in production — revisit if they end up on different domains.
- SQLite for dev (`DATABASE_URL` in `.env`); swap to Postgres for production via the same `DATABASE_URL`.
- Brand assets (logo, hero photo, exact colors) in `frontend/src/branding.ts`, `BrandMark.tsx`, `CitySkylineBackdrop.tsx` are placeholders reconstructed from the mockups — swap for the approved files when available.
- DRF extraction (`app/services/drf_extractor.py`) is OCR + grid-line detection, not an AI model — by design, per the plan's "AI handles document understanding, Python handles deterministic logic" split, this counts as deterministic since it's pattern/geometry matching against a known fixed form template, not a language model. It targets the current DRF template (Document Reference SSD-P-06 B/IQF.17); a template redesign would need it revisited. Extracted values are always shown as editable, pre-filled suggestions with a confidence indicator — never auto-submitted.
