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

## Setting up on a new PC (quick start)

Install **Python 3.12**, **Node.js**, **Git** and **Claude Code** (the AI reads the
DRF and the Design Sheets; nothing else does), and optionally **Tesseract OCR**
(document intake, document control and the submittal scanner read scanned page
numbers and stamps with it). Then:

```
git config --global core.longpaths true    # once per PC, before cloning (see below)
git clone https://github.com/mohamedr69/JUMAA-AL-MAJID---AI-PLATFORM.git
cd JUMAA-AL-MAJID---AI-PLATFORM
setup.bat        # once: Python packages, backend\.env with a new secret key, npm packages
start.bat        # every time: API on :8000, web app on :5173, opens the browser
```

The `core.longpaths` line matters on Windows: the company library keeps the
manufacturers' folder names, and its deepest datasheet path is 166 characters
before the clone folder is added. Without it, a clone into any folder longer
than about 90 characters -- a OneDrive-redirected Documents folder, say --
stops with "Filename too long" and a half-checked-out tree. `setup.bat` sets
it too, so later pulls are safe; only the first clone comes before it.

Clone into a **short folder** all the same -- `C:\dev` is what this was built
in. Git copes with long paths once told to; Python does not unless Windows'
own long-path setting is on, and a clone folder past about 90 characters puts
the deepest datasheets out of the library's reach. `setup.bat` warns when the
folder is too long.

Nothing else needs setting:

- **Database**: created on the first start (`backend/ep_platform.db`), with the
  default admin from `.env` (`admin@ep-platform.com` / `ChangeMe123!` -- change it).
- **Project archive**: the synced OneDrive library is found by name (below).
- **Company library** (documents, templates, stamp, datasheets): in `backend/library/`.
- **Compliance knowledge base**: `data base/Compliance_Response_Database.xlsx` is
  imported in the background on the first start.
- **Tesseract**: found on the PATH or where its installer puts it. Not needed for
  extraction: since 2026-09-17 the DRF and the Design Sheets are read by the AI only,
  and a sheet the AI cannot read is recorded as not read, with the reason.
- **AI**: Claude through Claude Code on the Claude subscription (`AI_PROVIDER=claude-code`,
  no API key). Install Claude Code and run `claude` once to sign in, as the same
  Windows user that runs the platform.

Projects, statements and approvals live in each PC's own database; they are not
shared by the repository. To carry them to another PC, stop the API and copy
`backend/ep_platform.db` (with `backend/uploads/` if documents were uploaded)
into the same place on the other machine; it is migrated on the next start.

## Setting up on a machine

Everything lives in this one folder: `backend/` (FastAPI), `frontend/`
(React), `docs/`. Nothing about the machine is written into the code --
paths come from `backend/.env`, and the project archive is found by itself.

```
git clone https://github.com/mohamedr69/JUMAA-AL-MAJID---AI-PLATFORM.git
cd JUMAA-AL-MAJID---AI-PLATFORM

cd backend
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # set SECRET_KEY and the AI key (see the comments)
.\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

cd ..\frontend
npm install
npm run dev                 # http://localhost:5173
```

**The project archive (OneDrive).** The platform reads the SharePoint
library that OneDrive syncs to every office machine, `SSD FIRE ALARM
PROJECTS - Fire Alarm 2021 Projects`. Nothing needs setting: at startup it
looks for that folder by name under the user's profile and the
`%OneDriveCommercial%` / `%OneDrive%` folders (`app/core/config.py`,
`find_synced_folder`). Only an archive kept somewhere unusual needs
`PROJECTS_ROOT` in `.env`. The archive is only ever read.

Optional on a machine: Tesseract OCR for reading scanned forms (below), and
`python scripts\sync_library.py` to fill `backend/library` with the company
documents (the folder structure is in the repository, the documents are not).

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
- The DRF and the Design Sheets are read by the AI (`app/ai/sheet_reader.py`, `app/ai/verification.py`): every page, twice, and a close-up where the readings disagree. One extra standard-tier call per sixteen rows against the earlier witnessed read. [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) is optional and used only by document intake, document control and the submittal scanner; set `TESSERACT_CMD` in `.env` if it is not on `PATH`.
- The deterministic readers (`drf_extractor.py`, `design_sheet_extractor.py`) no longer read documents; their parsers and page rendering are reused by the AI read. They are kept, with their tests, until the AI-only read has run on enough projects, and are then to be deleted.

### The company library

Everything a submittal needs that is **not about a particular project** -- the
company documents, the templates a package is built from, and the
manufacturers' datasheets -- lives in one local folder with a fixed structure
(`LIBRARY_ROOT`, by default `backend/library`). `backend/library/README.md`
documents the layout; `app/services/company_library.py` is the code.

These were read out of the synced archive, and that was wrong twice over.

**It was slow.** Every directory entry in a OneDrive tree is a
cloud-placeholder lookup. Indexing the Edwards datasheets took **30 seconds on
every start**, and none of that work is about the project being opened. Held
locally with the index kept on disk, the same 105 datasheets come back in
**0.06 s** after the first read -- measured, not estimated.

**It was not portable.** `Systems/01- FAVE/01- Edwards - UL&EN/01- EST4` is one
company's filing on one synced drive. A second company, or the same company on
a second machine, has neither -- which is most of what stopped this platform
being used for anything else.

A manufacturer is **discovered, not configured**: a folder under
`library/datasheets/` is a library named for the brand, so adding Menvier is
dropping a folder in, with no list to edit. `DATASHEET_LIBRARIES` remains as an
override for a library kept elsewhere.

```
cd backend
.\venv\Scripts\python scripts\sync_library.py            # copy it out of the archive
.\venv\Scripts\python scripts\sync_library.py --status    # what is held, and where from
```

The sync reads the archive and never writes to it, and skips files whose size
and timestamp already match, so running it again after one datasheet is added
copies that datasheet alone.

Three things make the move safe to do gradually:

- **The archive is still the fallback**, per document
  (`ARCHIVE_DATASHEET_LIBRARIES`, `ARCHIVE_SUBMITTAL_LIBRARY`). A machine
  part-way through works; it is only slower for what it has not got yet.
- **An empty folder is not a library.** The scaffold creates every folder of
  the structure empty, and taking one on existence alone hid the archive copy
  -- a package then came out with no company documents and nothing saying why.
  A shelf counts only once it holds a document.
- **The index is not in the library.** It goes under `CACHE_ROOT`
  (`backend/.cache`), because a library folder can be read-only or synced, and
  a cache written into a synced folder is uploaded for no reason. It is keyed
  by each file's size and timestamp, so a changed datasheet is re-read and
  nothing else is.

A library's file listing is trusted for `LIBRARY_RESCAN_SECONDS` (60 by
default): a BOQ's fifty part numbers are fifty lookups, and re-walking the
folder for each was the whole cost of the page. `POST
/design-rules/datasheet-libraries/reindex` is the way to pick up a datasheet
added a moment ago without waiting.

Git tracks the structure and ignores the documents: a trade licence, ISO
certificates and test reports are the company's own papers, and the Edwards
library alone is 435 MB. A clone gets the shape and fills it with the sync
script.

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

### After the ten-project review (13 September 2026)

Ten real projects were created and reviewed against their source forms
(`docs/REVIEW_FIXES.md` has the finding-by-finding record). What the review
found, and what changed:

- **The source folder is the folder the engineer chose.** Where an EP
  number matched two folders, the review form took the *first* match as the
  source folder while the DRF came from the *selected* one, so EP-31112 was
  filed under one contractor with its DRF under another. The resolver now
  returns `source_folder`, the form saves that, and creation refuses a DRF or
  Design Sheet outside the source folder (or a source folder outside the
  archive) -- which also closes the hole that let `/logs/file` serve any
  file under a client-supplied path.
- **The DRF's notes are read.** The `OTHER INFORMATION` block -- "device
  fixing only", "quoted as per BOQ", "single evacuation zone" -- was not
  extracted at all and was empty on all ten forms. It is found by its banner,
  read to the rule beneath it, and pre-fills the review form line by line.
- **Short brands are read.** "TOA" leaves fewer dark pixels than the
  threshold calibrated on "EDWARDS", so the PA/VA & BGM row came back
  unmarked on EP-30208 and EP-30387. Between a lower probe threshold and the
  calibrated one the cell is read and kept only at high OCR confidence.
- **Blank stays blank.** A low-confidence read made of letter fragments ("ee
  ee ee" on EP-31112's empty plot number) is dropped rather than offered as
  a value; and a value the block read misses altogether (EP-31725's plot
  number, centred alone in its cell) gets a sparse-text second read.
- **The right DRF is read.** The archive files the same form several times
  and the JPG copy of EP-30208's is the top half of the page only. A PDF now
  outranks an image, an original outranks a copy.
- **One system identity.** PA, VA, VAS and PAVA are one code (`PAVA`), VE is
  `VES`, everywhere: the resolver, the BOQ's manufacturer pre-fill, Compliance,
  the specification finder, the submittal builder, the document log. A
  "Design.pdf" with no code takes the DRF's one marked system where the DRF
  marks exactly one; where it marks two, the sheet stays unlabelled and the
  form says so. The review form lists every sheet with a checkbox and a
  system to choose.
- **One issue of each design.** EP-30175 filed FAS and VE sheets as R1, R2
  and unrevised side by side; attaching all of them read one system's BOQ
  three times over. Within a system the highest declared revision is
  selected by default and the rest are offered unticked, marked "superseded
  by ...". The reviewer can tick any of them.
- **Every box on a multi-building sheet.** EP-30208's PA design sheet is
  nine ruled boxes, one per building, over four pages; the extractor read
  the longest box on each page and lost 47 of 97 rows. Every box is read
  now, under the building banner above it, which becomes the section of each
  line's group heading ("DHAID - B2 BUILDING / f. Speakers"). The same part
  in two buildings is two lines, not a duplicate. The sheet reads 92 of its
  97 rows and 701 of its 700 quantity, against 50 and 232 before; the
  remaining rows are individual quantity misreads, which are dropped rather
  than guessed as they always were.
- **The battery sheet paginates.** A panel with more parts than the sheet
  holds continues its table on further sheets with the header repeated;
  EP-30175's FACP-01 clipped at about twenty of its parts. The sheet names
  the panel's own manufacturer -- from its BOQ lines, else the DRF's brand
  for its system -- and says "not established" rather than taking the first
  brand on the DRF; "EST4" appears only for Edwards. The screen's warnings
  that matter to the issued sheet (a lower-bound load, no selection) are
  printed beside the selection; whether the BOQ's own battery is short and
  the charger's compatibility are the page's business and stay off the
  sheet (platform owner, 16 September 2026).
- **The submittal builder says what it did.** The browser could not read
  the page-count header (`"? pages assembled"`); it is exposed now. The
  cover names only the fire alarm family for an FAS package and never
  "System System"; the divider's printed "PAGE 07" follows the section
  number; a build that fails names the stage and document that failed.
- **A session in use does not expire.** The cookie carried a fixed
  30-minute term from login; a request in its second half re-issues it,
  so a session ends only after 30 minutes of nothing.

Projects created before these changes are not rewritten. `python
scripts/repair_projects.py` lists, per project, the design-sheet codes, BOQ
line codes and source folder it would correct and why; `--apply` makes
exactly those changes. Where the platform cannot tell after the fact --
unassigned lines from two sheets of different systems -- it says so and
leaves them to the BOQ page.

### Selective AI assistance (GPT or Claude, off by default)

The platform is deterministic: OCR, geometry, rules, arithmetic. What it
cannot settle it now records as **issues** with a code, a page and a region
-- a quantity cell the parser could not read, a page with no recognisable
table, a design sheet with no system code -- and each read is stored as a
**run** with its coverage (every page: processed or not, and why) and an
outcome that cannot pass a partial read as a complete one. That happens
whether or not AI is on, and the BOQ page shows the rows needing review
with the cell's own image.

With `AI_ENABLED=true` (`app/ai`), a model is asked about the issues a
Python **router** (`app/extraction/issues.py`) marks eligible, and nothing
else. Which model is one setting: `AI_PROVIDER=openai` (the OpenAI SDK) or
`AI_PROVIDER=claude` (the Anthropic SDK), each behind the same interface,
with the same evidence, the same schema and the same validation. The
eligible issues are:

- an unreadable **quantity cell** -- it sees that cell's crop and its row,
  and proposes a number, which is *validated* only if an independent
  Tesseract re-read of the same crop agrees, else shown as unconfirmed;
- an unlabelled **design sheet** on a DRF marking several systems -- it
  sees the sheet's first-page words and the marked rows, and may suggest
  one of those codes; the reviewer picks;
- a **DRF field read at low confidence** -- a second reading of the cell
  image, validated the same way.

Never: a blank field, a missing datasheet current, a revision choice, a
source-folder mismatch, an unsupported file, or any arithmetic. Layout
interpretation for unrecognised sheets is routed but disabled until a
labelled evaluation set exists.

A proposal moves nothing. Accepting one on the BOQ page adds the row as the
engineer, through the same rows the BOQ editor writes, and is refused if
the engineer has since typed that line in. Results are cached by content
hash plus task, evidence, parser, prompt, schema, model and policy
versions -- never by filename -- and identical simultaneous requests share
one call. Budgets (`AI_MAX_*`) are reserved before each call and reconciled
after; a limit that trips leaves the issue *starved* and the run
`BUDGET_EXHAUSTED`, never "done". `GET /admin/ai/usage` is the diagnostics
view: calls, tokens, cost, cache hits, latency.

Setup: `pip install -r requirements.txt`, then in `backend/.env` set
`AI_ENABLED=true`, `AI_PROVIDER`, `AI_MODEL_SMALL` / `AI_MODEL_STANDARD`
and `AI_API_KEY` (that file is gitignored; the vendor's own environment
variable works instead if you prefer). Model IDs are settings, checked
against the account's own model list rather than assumed. Prices start at
zero on purpose -- a made-up price is worse than none -- so set
`AI_PRICE_INPUT_PER_MILLION` and `AI_PRICE_OUTPUT_PER_MILLION` from the
vendor's pricing page to turn on the cost column and the per-job cost cap;
the call-count and time limits bind either way.

`python scripts/ai_selftest.py` makes one short call on a drawn cell and
says whether images, the schema and the validation all work on the
configured model (`--all` tries a shortlist). Tests use a scripted
provider; nothing in the suite calls a live model.

### The folder is searched once; the database remembers

Three things used to walk the project's OneDrive folder on every open, and
now do not:

- **Specifications.** The compliance page's search of the folder is run
  once and what it found is kept on the project (`projects.spec_locations`,
  `spec_warnings`, `specs_found_at`); every later open reads the locations
  from the database and goes straight to the files. The folder is searched
  again on "Search project folder again", or by itself when a file it found
  is no longer where it was. The submittal package's specification section
  reads the same record.
- **Material submittals.** The map and the register come from the database;
  opening the tab reads nothing else. The folder is read by "Sync documents"
  (below), which re-reads only the forms that changed and rebuilds the map
  from the stored readings.
- **A user's data on every PC.** The database is a file under `backend/` by
  default, which is why a project made on one PC is not on another. Set
  `DATA_ROOT` in `backend/.env` to a folder OneDrive syncs
  (`%USERPROFILE%\Juma Al Majid\EP Platform`, say) on each PC and the
  database, the uploads, the backups and the caches live there together; the
  first start with it set copies a database already under `backend/` into
  it, and the same sign-in then finds the same projects everywhere. One PC at
  a time: close the platform before opening it on the other, so OneDrive
  finishes syncing (the database is kept as one file, no write-ahead log,
  for that reason). A shared database server (Postgres via `DATABASE_URL`)
  is the answer for several people working at once.

### The document index and "Sync documents"

The rule for every tab -- Logs, Material submittal, BOQ, Compliance, Battery
calculation -- is that opening it touches the database only: no AI call, no
walk of the OneDrive folder. OneDrive holds the source documents; the
database holds the working data and every extraction; Python does the change
detection, the dependency tracking, the validation and the arithmetic; the
AI is called only for a document that is new or changed, or on an explicit
request.

- **The index** (`project_documents`, `app/services/document_sync.py`) has
  one row per file in the project folder: path, role (`drf`, `design_sheet`,
  `submittal_form`, `spec`, `document`), size, modified time, content hash
  (`sha256`, the cache key with the file), what was read from it
  (`reference`, `revision`, `status`, `extracted`, a link to the stored AI
  `reading_id`), `state` (`fresh`, `stale`, `processing`, `failed`,
  `removed`), `last_processed_at` and `index_version`.
- **The sync** (`POST /projects/{id}/jobs/sync-documents`, a job) stats
  every file and compares size and time with the row, then the hash for a
  file whose stat changed: a new file is processed, a changed file is
  processed, an unchanged file is skipped, a missing file's row is marked
  `removed` (its data is kept). Processing a submittal form is the one
  place the AI is called, and the reading is stored by content hash first,
  so the same PDF is never read twice. A file that fails to read keeps its
  previous result and is marked `failed` with the error; the next sync tries
  it again. The first sync of a project is its initial processing, started
  by itself from the project home once; after that only the button reads
  the folder. The result says how many files were new, changed, unchanged,
  removed, failed and read by the AI.
- **Dependencies** (`document_dependencies`): what was built from which
  document -- the BOQ and Project Info from the Design Sheets and the DRF,
  the compliance search from the specifications, each register row from its
  form -- with the hash it was validated against. When a source changes the
  sync marks its dependents stale (`GET /projects/{id}/documents/status`
  lists them, and the "Project documents" card on the pages shows them);
  nothing is rebuilt by itself. BOQ -> Re-read, the AI check on Project
  Info, and the compliance page's next search are the explicit rebuilds. A
  new specification drops the stored search so the compliance page searches
  again.
- **The pages.** Logs list the index's rows (with revision history: an
  older revision with a later one is `superseded`, never left "under
  review"); the material submittal map is the stored map; the BOQ is the
  saved BOQ; compliance loads the stored specification locations and
  checks a clause only when asked; the battery calculation is Python
  arithmetic over stored inputs (the BOQ quantities, the equipment current
  table) with no AI in it, and each panel's result is kept
  (`battery_panel_results`) under the hash of its own inputs -- its BOQ
  lines, its settings, the currents of its parts, the batteries on file --
  so opening the page reads a panel back while those stand, recalculates
  only a panel whose inputs moved (a BPS quantity changed: that BPS alone),
  and, when a recalculation fails, keeps the previous figures visible
  marked stale with the reason (`reused_panels` / `recalculated_panels` in
  `GET /projects/{id}/design/battery`). The compliance statement does the
  same for its clauses: when the BOQ, the scope, the knowledge base or the
  specification moves, only the rows answered from the knowledge base or
  the model are flagged for recheck; the engineer's answers stand.

### The project folder structure, and filing a built submittal

Opening (or creating) a project makes the folders every project keeps under
its archive path (`app/services/project_folders.py`) -- only the missing
ones; nothing that exists is moved or renamed:

    01- Scan\                                   (only when no scan / commercial folder exists)
    02- Material Submittals\{FA, ELS}\R0\        a folder per system, a folder per revision
    02- Material Submittals\Approved\{FA, ELS}\  the stamped copies the consultant returns
    03- Drawings\IFC\Electrical\{ACS, FA, Light, Power}\
    03- Drawings\IFC\Mechanical\{FF, SM}\
    03- Drawings\IFC\{RCP, Builder Work}\
    03- Drawings\SD\{FA, ELS, Approved}\

Every folder is indexed by "Sync documents", so the logs list what arrives
in them. A material submittal package the platform builds is filed as it is
built (`app/services/submittal_filing.py`): written to
`02- Material Submittals\<system>\<revision>\EP-xxxxx - Material Submittal -
<system> - R<n>.pdf` and, because the platform knows exactly what it made,
entered in the same step in the document index, as a stored form reading
(reference `EP-xxxxx-MAS-<system>`, revision n, under review), in the
register and in the log -- so the Material Submittal tab, the map and the
log show it at once with nothing scanned and no model asked. A rebuild of
the same revision replaces the file; `file: false` on the build request
gives the download only.

### The AI reads the material submittals

The consultant's reply on a material submittal form is a stamp or a
hand-written comment, and the OCR read of it was not reliable; the form is
now read by the model (`app/ai/submittal_reader.py`): the first two pages
of every PDF in the project folder that looks like a submittal form, as
images, once -- each reading is stored by the file's content
(`document_readings`) and never read again. A form is a first page with
a MAS reference; or a "Submittal No. / Ref." under a MATERIAL SUBMITTAL
heading, in whatever form a contractor uses (EP-29495's transmittal
serves its shop drawings, method statements and prequalifications too,
told apart by that heading); or JAM's own package cover ("MATERIAL
SUBMITTAL FOR ...") or a scan, filed under a submittal / approval folder.
A file whose path passes 260 characters is read through the long-path
API, as the logs read it, instead of being skipped. The model names the
**system** the submittal is for (`system_code`), reasoning from the
title, the materials and the manufacturer, with emergency lighting under
every name it goes by -- emergency / exit light, self-contained or
self-monitored emergency light, EML, central battery system (CBS) -- all
one system, ELS; the folder and the wording settle it when the model
does not. The sync draws the **map**: per system, a row per submittal
reference and a column per
revision (R0, R1, ...), each cell UR (submitted, no consultant reply),
A, ANN (approved as noted), RR (revise and resubmit) or REJ, a reply
counting only when the model saw it was the consultant's; the same
revision filed twice is settled by the copy carrying the reply. The map
names the **actions**: a revision returned RR or REJ with no later
revision filed, or a project with no submittal filed at all, is
"Material submittal required". The register follows the map (latest
revision and where it stands); `GET /projects/{id}/submittals/map` has it.

### The equipment current table

`equipment_currents` (`app/services/equipment_currents.py`, the "Equipment
currents" page for admins) is what each part of a fire alarm system draws,
settled once for every project: the Edwards parts that are metalwork
(backboxes, chassis, doors, brackets, filler plates, the BC-1 battery
cabinet), the parts built into another module (4-COMREL is on the 4-CPU
board), and the devices with their standby and alarm figures and where
each came from. It is seeded with the parts the platform owner settled on
2026-09-16 and with the catalogue's datasheet-read figures, and it grows:
a figure typed in on a battery page, a datasheet read, and an engineer's
"confirm: no current" or "it draws current" are all written here as well.

Every row refers to its datasheet in the library as a link (`datasheet_library`, `datasheet_path`, `datasheet_pages`, how it was matched -- "filename" / "family" for the part's own sheet, "text" for one that only mentions it -- and the sheet's content hash), filled wherever a figure is read and, for a no-load part, from the sheet that lists it; the Equipment currents page opens the sheet at the page. A figure is trusted from a text-only match only when the reader placed it under the part's own name; otherwise the sheet is a candidate to confirm, not a source. "Audit datasheets" on that page links what is unlinked and reports the rest: parts with no sheet in the library, links that no longer resolve, sheets that changed since (the hash), and figures the sheet no longer gives or gives differently.

The battery calculation asks the table first. A part in it is recorded in
the catalogue as confirmed and the page asks nobody -- the old
"Confirm parts set to draw no current" list is only for parts the table
does not know, and a part answered once joins the table and is never asked
about again. `AI_MODEL` has nothing to do with it: the table is data, kept
in the database, edited on the page.

### The AI reads the documents; the OCR read is the witness

Selective assistance only ever showed the model rows the OCR had located,
so a Design Sheet whose layout the extractor did not know came back with
no lines and nothing for the AI to check -- and every new sheet layout was
that sheet. The roles are now the other way round (`app/ai/sheet_reader.py`):

- **The model reads every page** of every Design Sheet on the project's
  first BOQ open, in overlapping bands, reporting each row -- item, section
  banner, heading or other -- with its values and its box on the page. Both
  tiers are **Claude Fable 5.1** (`AI_MODEL_SMALL` / `AI_MODEL_STANDARD`).
- **The OCR read is the witness.** It is never shown to the model. A row the
  OCR read with the same quantity is a line. A row the OCR could not read
  (or a sheet it could not read at all) gets a second, independent AI
  reading of the row strips; two AI readings that agree make the line. A
  disagreement gets one close-up at full scan resolution, which settles for
  whichever reading it agrees with; if nothing agrees, or the model could
  not read the quantity, the row is a **row to review** with every reading
  beside it. A row only the OCR found is a line only when the model reads a
  quoted item in its close-up.
- **The reading is stored for good** in `document_readings`, keyed by the
  document's content hash. A document with the same content -- the same
  project reopened, another project filed with the same sheet -- is read
  out of the database and calls no model. The DRF is read the same way by
  the AI check of Project Info (`app/ai/verification.py`), and its reading
  is stored beside the sheets'. The AI check reuses the sheet reading for
  every row it settled, so a check after an AI read costs nothing for those
  rows; the second AI readings it does make are of different images (fewer
  rows, nearer the scan's resolution, another cut of the DRF), so they are
  independent looks even by the same model.
- **The first read runs as a job.** A scanned multi-page sheet takes the
  model minutes, so `POST /projects/{id}/boq/ensure` starts a `boq_read` job
  and answers with it; the BOQ page follows the job and loads the lines when
  it ends. A project whose sheets were all read before is read out inline,
  as before. `AI_READ_*` bound the calls and the time; `AI_DISABLED_TASKS=read_sheet_page`
  switches the whole-sheet read off (the OCR read stands alone, as before)
  without touching the cell-level assistance or the AI check.

### A scanned part number is settled against the catalogue

The model reads a catalog number as printed, so its reading stands as
evidence -- and a scan prints SIGA-AA50 as "SIGA-AASO" (S for 5, O for
0). The part library (`boq_provenance.part_library`: the equipment
current table with its aliases, the catalogue's part currents and battery
units, the models the knowledge base names) settles the code: an exact
match, or a match with one scan confusion, gives the BOQ line the
catalogue's number, with the reading kept beside it (`catalog_match.read_as`,
`catalog_raw`). The AI check compares the two readings the same way, so
"SIGA-AASO" read off the sheet agrees with the BOQ's SIGA-AA50 and is not
flagged, and a check settled on the model's spelling still writes the
catalogue's. A code the library does not know is left as read.

### Re-reading the documents

Both reads above happen once. The DRF is read to fill the review form at creation and never looked at again; the Design Sheets are read into the BOQ on first open and the `boq_extracted_at` stamp stops that repeating. So the archive moves on and the project does not -- a re-scanned DRF, a Design Sheet filed a week later, a field mistyped at review: nothing surfaces any of them.

`POST /projects/{id}/reextract` (`app/services/reextraction.py`) re-reads both, **every time it is called**, and reports what differs. Sheets come from the project folder as it stands now rather than from the stored `project_design_sheets` rows, which is what lets a sheet filed after creation appear at all -- it is reported as `new`, and its lines are the ones missing from the BOQ entirely.

**It writes nothing.** That is the point of the split: the stored values are the engineer's corrections, and a re-read that applied itself would be exactly the regression `boq_extracted_at` was introduced to stop. Differences are reported for someone to apply through the ordinary edit paths (`PUT /projects/{id}` for the fields, the BOQ table for the lines). It is POST rather than GET because it costs OCR over every sheet, and should not be run by a page render or a refresh.

What counts as a difference is deliberately narrow, so the report stays worth reading:

- **Fields** compare on letters and digits only. `"Wadi Al Safa 5,  DLRC ,Dubai."` against `"Wadi Al Safa 5, DLRC, Dubai"` is a match, not a change -- OCR spacing and punctuation noise would otherwise bury the real differences.
- **A field OCR cannot read is `only_stored`, not a conflict.** The engineer typed it in *because* the scan was unreadable; flagging that on every run would train them to ignore the report.
- **BOQ lines** are paired by the same `line_key` an issued-revision comparison uses (system, group, part number, description), and only the columns a sheet actually carries take part. Manufacturer, unit, prices and remarks are filled in by the platform or the engineer and are not on the sheets -- comparing them would report every line as changed on every run.
- A broken DRF does not lose the BOQ comparison, and an unreadable sheet does not lose the rest of the report.

Tests are in `tests/test_reextraction.py`: the synthetic ones stub both extractors and pin the comparison rules, and one opt-in live test builds a real project from its DRF and re-reads it, which is what catches an extractor regression -- a field that quietly starts losing its first word shows up there as a difference against a project built from the previous read.

### Working with the BOQ

The page follows the platform owner's design: the project and revision at the top with Export BOQ and Add Item, then the **sources** a BOQ can come from.

- **As per Design Sheet** -- what the platform reads and what the table below edits. Under it are the totals (items, quantity, systems, estimated cost from the lines' prices), the per-system tabs, and the editable table, paged at 25 lines.
- **BOQ Floor Wise** -- planned for a future release. It will show what each system needs on each floor from the schedules engineers keep in the project folder, which is useful for shop drawings and site deliveries.
- **As per IFC Drawings** -- not built, and says so (uploading the issued-for-construction drawings, taking quantities off them and reviewing them against the design-sheet BOQ).

Sections of the design still being built -- Drawings, O&M Manual, Reports, Project Team, Settings -- are in the left nav marked "soon" and open a page saying the system is under maintenance. They are listed rather than hidden because the nav is the map of the workspace; what is not built says so instead of pretending.

### Documents

Documents sits straight after Project Info and lists what the resolver matched in the archive: the DRF, the Design Sheets and the project folder. **Whatever it did not find can be uploaded there instead** -- an upload button sits next to each document that is missing, including a named one for every system the DRF marks but no Design Sheet was found for ("No Design Sheet was found for EML, which the DRF marks for this project"), plus one for adding a sheet under any code. A DRF that was found can be replaced; a Design Sheet can be detached from the project, which never deletes the file.

Uploads go to `UPLOADS_ROOT` (a folder per project), not into the archive: the platform reads the project archive and never writes to it. Only PDF and Excel are accepted, since those are what the extractors read, and 60 MB is the limit. Two things an upload deliberately does not do: a DRF's fields are not re-read (they were reviewed at creation and are edited under Project Info), and a Design Sheet added later is not read into the BOQ, because that read happens once per project -- the page says so where it is uploaded.

### Calculation

The design calculations share one section after BOQ, with a tab per system: **Battery** (panel standby batteries, below) and two marked "soon", which say so when opened -- **Amplifier** (Voice Evacuation amplifier loading, below) and **Power**. Each tab is its own route (`/projects/{id}/calculations/battery|amplifier|power`), so a calculation can be linked to; the pages' former paths (`/batteries`, `/design/ve`) redirect there.

- **Columns** follow the plan: system (the tab), group, manufacturer, model / part no., description, quantity, unit, remarks -- plus unit and total price, which the Design Sheets have as columns. Prices are blank on every sheet in the archive, so they are kept for the engineer to fill rather than extracted.
- **Filter and totals.** The filter matches part no., description, manufacturer, group, unit and remarks within the current tab. The totals under the table sum the numeric quantities of the lines shown; lines quoted as a word ("Lot") are counted separately rather than guessed at.
- **Possible duplicates** are flagged, never removed: same system, same group heading, same part number and same description (ignoring case, spacing, punctuation). The rule is narrow on purpose. The same part legitimately recurs across a BOQ -- every panel has its own CPU and batteries -- and a part-number-only match flagged 93 of the 279 lines on the three real projects, nearly all correctly listed. This rule flags 3 pairs there, each an item listed twice under one heading. The rule lives in `frontend/src/lib/boq.ts`, since it has to follow unsaved edits.
- **Export** (`GET /projects/{id}/boq/export.xlsx`) writes the *saved* BOQ: a Summary sheet with project details and per-system totals, then one sheet per system in tab order. Numeric quantities are real numbers and the per-sheet totals are live `SUM` formulas, so they follow edits made in Excel. The button is disabled while there are unsaved edits, which would otherwise be missing from the file without any sign.
- **Revisions.** Issuing a revision (BOQ -> Revisions) freezes the saved BOQ as Rev 00, Rev 01, ... with a note, who issued it and when. It is stored as a snapshot, not as references to BOQ rows, because every save replaces those rows wholesale. There is no endpoint that edits or deletes a revision. Issuing is refused when nothing has changed since the last one, which also stops a double-click issuing the same BOQ twice. Any two revisions, or a revision and the current BOQ, can be compared (`GET /projects/{id}/boq/compare?from_rev=0&to_rev=1`): lines are paired on system + group + part no. + description (ignoring case, spacing and punctuation), so replacing a part number reads as one line removed and another added, while a quantity or price edit -- or tidying a line's text, such as removing an OCR artifact -- reads as a change to the same line. Reordering lines alone is not a change. The "nothing has changed" check on issuing uses this same comparison, so the page and the server agree on what can be issued. Each revision exports to Excel on its own.

### Voice Evacuation amplifier loading

**The Amplifier tab is marked "soon" and opens the under-maintenance page**, on the platform owner's instruction. Nothing below was removed: the page component (`frontend/src/pages/ProjectVoiceEvacuationPage.tsx`), the API and the calculation are all still here and still tested, and releasing the tab is putting the component back on the `amplifier` route in `App.tsx`. It is described here as it works, because that is what comes back.

A project's VE design is imported from the engineer's amplifier calculation workbook, picked from the project's archive folder, and is then an editable schedule: zones with their speaker counts, the tap wattage of each speaker type, the channels (runs of zones on one amplifier output, with its rating) and the racks / APS cabinets that group channels. It is stored as one JSON document per project (`project_designs`, validated by `app/schemas_design.py`). Results are never stored: every read recalculates them (`app/services/ve_calculation.py`), so a figure cannot disagree with the counts it came from.

**The arithmetic is the engineers' own**, read off their workbooks: zone watts = sum of count x tap, a channel's required watts = the plain sum of its zones, a rack's totals = the sums of its channels. Tested against two real workbooks (EP-24601, EP-29495), whose every zone and channel figure it reproduces.

**The pass/fail is not.** No workbook in the archive checks the load against the amplifier -- the rating is typed in by hand and nothing compares the two. The limit applied here -- a channel fails above **80% of its amplifier's rating** (40 W on a 50 W amplifier) -- was set by the platform owner and is stored as a `design_rules` row (`ve.limit` / `amplifier_max_load`), seeded at startup, with its `source`. Note the archive's own "20% spare" convention (EP-17428) is different arithmetic: required x 1.2 <= rating, i.e. ~83%, under which EP-24601's 42 W channel still fails but 41 W passes. Rules are versioned, never edited in place: to change the limit, mark the current row superseded and add the next version. A design copies the fraction (and the rule id) when it is imported, so a later rule change does not silently move a calculation already made; re-importing picks up the rule in force.

**Reading the workbook** (`app/services/ve_workbook_reader.py`) is by headings, not positions, because there is no template: of nine amplifier workbooks sampled from the archive no two share a layout. "Watts per Area" / "Load in watt" (or failing that, two speaker headings) marks the header row; speaker columns are the ones headed as speakers or carrying a tap ("WS-(.5W)"); taps come from a "Tapping Wattage" row or from the heading; a value in "Required Watts" / "Total Wattage" / "AMP W" starts a channel; "Proposed Amplifier" gives its rating ("2 x 50 W" is 100); "Rack" / "APS" groups channels. One headerless layout (EP-29495: models over taps, nothing labelled) is recognised separately and only accepted if its watts column agrees with its counts. The schedule ends at a totals row ("Grand Total") or a note ("Result"). A speaker column that has counts but no tap is refused rather than guessed. 8 of the 9 sampled workbooks read; the ninth (EP-16884) lists one speaker total per circuit with no tap per type, and is refused with a message.

The workbook's own computed figures are imported too, but only to **flag where the sheet disagrees with its counts**. It happens: EP-23315's AMP-2 is merged over four floors but its formula sums two (sheet 38.5 W, counts 53 W), and EP-15792's basement row says 81 W for counts that give 83.5 W. Where the sheet's required watts include spare ("with 30% Spare"), they are not compared. Anything else the reader notices (a rack starting partway through a channel, two ratings on one channel) is stored with the import and shown on every visit.

### Panel standby batteries

Project -> Panel Batteries sizes each fire alarm panel's standby battery from the **saved BOQ** and checks it against the battery the BOQ quotes (`app/services/battery_calculation.py`, `GET /projects/{id}/design/battery`).

**The method is the engineers'**, and three of their workbooks agree on it (EP-20779, EP-30784, and EP-29076's sheet copied into EP-30784): required Ah = (standby mA x 24 h + alarm mA x 30 min) / 1000 x 1.2. The durations, the 1.2 and the 24 V panel voltage are a seeded `design_rules` row (`battery.sizing` / `fas_panel`) with its source; the platform owner confirmed them. EP-20779's three EST3 panels are the test oracle: with the currents the engineer used they come out at the engineer's 60.33 / 32.35 / 26.64 Ah and 65 / 42 / 42 Ah batteries.

**Panels come from BOQ groups.** A group whose heading names a panel ("Fire Alarm Control Panel", "FACP", ...) is one panel type; its heading line (the line with no part number, "... Includes:") gives how many identical panels it quotes, and the lines under it are **per panel** -- the sub-panel groups quote one chassis and one backbox for two panels, which only reads one way. Amplifier / booster power supply groups (APS, BPS) are sized too, the way the company's EST4 BC template's APS and BPS sheets do it, but **once per cabinet type** however many the BOQ quotes (18 amplifier closets are one APS calculation: every cabinet carries the same load), named APS and BPS rather than FACP-nn. Their figures come off the datasheets like every other part's, never typed in from the template: a booster is read by its own rule (`datasheet_currents.read_power_supply_current`) -- standby its internal supervisory current, alarm its internal alarm current plus the full rated NAC output (BPS10A: 70 / 10 270 mA); an APS cabinet's calculation is its amplifiers and modules as the BOQ quotes them (SIGA-AA50 2 mA / 2.8 A full load, SIGA-CT2 396 / 680 uA), the power supply unit itself settled as no load; a module's microamperes and an amplifier's amperes are read in those units and kept in mA. These parts are read off the library's datasheets into the equipment current table when the platform starts (`equipment_currents.DATASHEET_SEEDS`). The exported sheet lists only the parts that draw current. Selection is the smallest catalogued battery that covers the requirement on its own (8.19 Ah -> 2 x ES18-12 in one 24 V string), the catalogue being the ROCKET datasheets in the library (`12- Battery_Rocket`), read by model and rating wherever the two lines fall on the sheet. A repeater panel is not sized: it is powered by the panel it repeats. Every BOQ group is shown on the page with what was done with it, so a panel whose heading does not match is visible as "not calculated" rather than missing. On the readiness dashboard a quoted battery smaller than required is a warning to check, not a block.

**Currents come only from datasheets.** A part's standby and alarm mA is a `design_rules` entry (`part.current`, keyed by part number) that an engineer enters from the datasheet, with the datasheet named as its source -- nothing is seeded, and a value without a source is refused. Mechanical parts (chassis, filler plates, cabinets, doors) are recorded once as "no electrical load". Batteries are recognised from the battery catalogue (`battery.unit`) or, failing that, from the BOQ's own description ("Battery, 12 V @ 65 AH"), and are not loads.

**A missing current makes the load a lower bound.** Until every part in a panel has a current, the sum is only part of the load: it can show a quoted battery is already too small ("battery too small", shown as "â‰¥ N Ah"), but a panel with a missing part is never reported as sufficient, and no battery is proposed for it. The same holds for a module whose quantity is not a number, and for a panel group that adds up to 0 mA (its modules not itemized -- a lump-sum line, or rows lost to OCR). What the page cannot know is a module the Design Sheet read dropped altogether: check a panel's module list against its schedule.

**Selection** follows the engineers: the smallest battery that covers the requirement on its own; failing that, as many of the largest as fit plus the smallest that covers the rest, as parallel 24 V strings (80.2 Ah from 26 / 42 / 65 -> 65 + 26, as EP-30784's sheet selects). Batteries are selected **from one brand**, held as a `design_rules` row (`battery.selection`: ROCKET, the platform owner's decision), and read from that brand's datasheets in the library: a battery datasheet is recognised by its own heading ("ES 65-12" over "12V - 65Ah", the model having to agree with the rating under it), so dropping ES 26-12 or ES 100-12 into the library is all it takes to widen the choice. Catalogue entries (`battery.unit`) of the same brand join them. Today the library holds ES7-12 and ES65-12, so EP-30784's main panel (110.88 Ah) selects 4 x ROCKET ES65-12 -- two 24 V strings, 130 Ah -- and its sub-panels (42.6 Ah) 2 x ES65-12. No per-charger limit is applied -- if a power supply caps its battery, that is a datasheet value to add.

The battery the **BOQ quotes** is no longer the panel's battery, only a check: `quoted_short` says the quote is smaller than the requirement (EP-30784's main panel quotes 65 Ah against 110.88 Ah), which the page and the export show beside the selection. A panel's status is `ok` (load fully known and a battery selected), `incomplete` (a current missing, a quantity unreadable, or nothing itemized) or `no_selection` (no battery of the brand on file).

**Datasheet libraries.** Each manufacturer's datasheet PDFs live in one folder of the company library (`library/datasheets/<BRAND>`), and a folder there *is* a library -- see "The company library" above. **Currents are filled from the datasheets automatically.** When an engineer opens Panel Batteries, every part without a current is looked up (`POST /projects/{id}/design/battery/fill-currents`). Its datasheet is found in the library (`app/services/datasheet_library.py`): by filename first ("01- 4-CPU.pdf"), then a datasheet named for its family ("4-NET.pdf" for 4-NET-TP), then any datasheet that mentions it. Its standby and alarm mA are then read off (`app/services/datasheet_currents.py`) and recorded in the catalogue with the datasheet, document number and page as the source. Only parts without a current are touched, so an entry someone corrected is never overwritten.

Datasheet tables are not uniform, so a figure is placed by geometry. Its label is the nearest Standby / Alarm / Current word. Its model is a model named right after it, else the column of a header row naming several models, else the nearest bold section title above it on that half of the page, else the part its datasheet is named for. Headings are told from row labels that merely start with a model by their type (bold, medium or larger). Where a datasheet gives more than one figure, the **worst case** is filled, since a larger figure can only oversize a battery: 4-USBHUB 560 mA fully loaded rather than 44 mA idle; 24L24S 3.0 mA + 0.23 mA Ã— 24 indicators = 8.52 mA; 4-NET-TP 45 mA (CAT5e) rather than 32 mA. Figures per supply voltage are taken at 24 V. The 4-CPU's "Alarm: see the 4-COMREL" takes its standby figure, the relays being their own BOQ line. Figures that belong to another model are never borrowed: 4-2ANN's datasheet gives the 4-ANNCPU's current, and 4-2ANN is left for the engineer. A part with no datasheet current whose BOQ description is mechanical (chassis, filler plate, backbox, cabinet, door, bracket) is recorded as no load. Anything else is listed on the page with the reason and a small entry form. For EP-30784 that is 4-COMREL (no datasheet in the library) and nothing else; its 12 modules read are pinned by live tests.

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

### Compliance statement

A compliance statement is written clause by clause against the consultant's specification, so Project -> Compliance Statement starts from the question of whether the project has one. It has a **tab per system the project delivers** -- taken from its Design Sheets and BOQ, so EP-30784 shows FAS and EML (the DRF's marks are only the fallback for a project with neither: on their own they list more than the project builds) -- and searches the project folder for each system's specification (`app/services/spec_finder.py`, `GET /projects/{id}/compliance`).

Specifications arrive in three shapes and all three are read: **a section of its own** named for what it covers ("283111 - ADDRESSABLE FIRE DETECTION AND VOICE EVACUATION.pdf"); **the same, zipped**, which is how they arrive from the estimation team, so archives are looked inside and a member is served straight out of the zip; and **one electrical specification covering every division**, where a system's section is a run of pages -- found by its heading and reported as a page range, since that is the part the engineer needs. Matching is by CSI section number (28 31 11, 26 52 00) and by the words a section is titled with, and only files that read like a specification are opened, so a fire alarm *layout*, a submittal or the compliance statement itself is never offered as the spec. On EP-30784 that finds 283111 for FAS and 265200 for EML, the fire-suppression sections beside them left alone.

Beside the specification sit the two actions: **preparing** the statement clause by clause (`app/compliance/service.py`, `POST .../compliance/prepare`) and **checking** a submitted one against what the specification asks (`.../compliance/check`).

**The compliance knowledge base** (`app/knowledge/`) is what *Auto-fill* answers from. Its source is the Compliance Response Database -- a folder synced from OneDrive whose file of record is `Compliance_Response_Database.xlsx`: 1,657 past submittal documents read into 62,519 distinct requirements, 77,475 response variants and 130,605 response-source links, each with the exact wording, the manufacturer offered, the specification family/section/clause and the source PDF page. The folder also holds the same records exported as Markdown (`knowledge/`, `agent_bundle/`), indexes and the extraction's working files (`_work/`); an import inventories all of them and reads only the workbook, so nothing is imported twice. *Update knowledge base* (Settings > Knowledge base, administrators; or `python scripts\import_compliance_knowledge.py`) hashes the workbook, skips it when unchanged, validates its sheets, and writes the `knowledge_*` tables of the application database in one transaction -- a failed import leaves the last usable knowledge standing, and records a later workbook no longer carries go inactive rather than away. The source folder is a server setting (`COMPLIANCE_KNOWLEDGE_SOURCE`); it is never queried at run time and its path is never sent to the browser. Refreshing makes no model call.

**Autofill** (`app/knowledge/autofill.py`) is deterministic and calls no model. Whether a record may be reused at all is decided at import by the policy in `app/knowledge/policy.py`: no review flag (OCR text, position-based pairing, inferred columns, merged rows, conflicting statuses), a current -- not superseded -- source read from native text at high confidence, a status the statement can carry as written (Comply, Noted, Comply with Qualification), a confirmed manufacturer, a requirement long enough to be a clause, and no blocking review issue. That leaves about 14,000 eligible responses. Per clause the requirement wording must match after a conservative normalisation (whitespace, quotes, case, a leading outline label -- never numbers, units, negation, operators, editions or model suffixes) or through an equivalence an engineer validated; the response's manufacturer, and any model it names, must be what the project's BOQ proposes (a "Noted" commits to no product); and commitments it carries (installation, testing, training, warranty...) must be in the project's scope of work. The outcome per row is *eligible* (a draft, historical status proposed), *candidate* (a similar or blocked answer to look at), *missing model*, *scope verification*, *conflict* or *no match* -- and only the first writes anything. Similarity, clause numbers, families and how often an answer was given never fill a row.

**Review with AI** is one clause, on the engineer's click (`app/knowledge/review.py`): the clause, its parent heading, the BOQ lines that bear on it, the scope excerpt, up to three past answers, the draft and the instruction -- never the whole specification or database. The reply is structured (response, proposed status, evidence references, missing information, deviations, notes), checked against the vocabulary and the ids it was given, kept on the row so reopening it costs nothing, and shown beside the draft with Accept / Edit / Reject; a request id makes a double-click one request, a failure is not retried. Accepting is not reviewing: every row carries a **workflow status** (unfilled, auto-filled draft, candidate, AI suggestion pending, engineer reviewed, needs recheck) beside its **technical status** (complies, does not comply, partially, insufficient evidence, not applicable), proposed until *Mark reviewed*. Every change is written to `compliance_audit` with the specification, BOQ and scope hashes and the knowledge version it was made against; when any of those change, the rows that came from the knowledge base or the model are flagged for recheck, their text untouched.

**Backup and deployment.** The knowledge lives in the application database (`DATABASE_URL`), so it is backed up and restored with it: copy `backend/ep_platform.db` (stop the server first, or use `sqlite3 ep_platform.db ".backup copy.db"`), or, on Postgres, the usual dump; `alembic upgrade head` recreates the empty tables and a re-import refills them. Several backend instances share one database, never local copies.

**Where there is none, the page offers the two ways forward:** *"No specs available â€” prepare draft mail to contractor"*, which drafts a mail from what the project records (the contact, the system, the section numbers usually carrying it, and a request to confirm in writing if the project has no specification, so the statement can be written against the UAE Fire and Life Safety Code instead) to copy or open in a mail client -- the platform never sends it; and **upload**, for the copy the engineer has, which is kept under `UPLOADS_ROOT` and then listed like any other. The search takes about 40 seconds on a synced archive, so its answer is held for 15 minutes with a "Search again" button beside it.

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

The left nav lists Home, Project Info, BOQ, Panel Batteries, Material Submittal and Documents. VE Amplifiers was taken off it on the platform owner's instruction, and the Amplifier calculation tab is now marked "soon" as well; the API remains at `/projects/{id}/design/ve` and the page component is kept for when it is released.

### User records: account, projects and activity

Every user has a record of what they did on the platform, kept in the `activity_events` table (`app/services/activity.py`): signing in and out, opening a project (once per 30 minutes), creating, editing and deleting a project, saving the BOQ and issuing a revision, accepting or rejecting a reading-review line, uploading and removing documents, creating, changing, scanning and packaging submittals, saving calculations, and each step of a compliance statement (prepare, check, edit, auto-fill, AI fill, approve, withdraw, delete). An admin's changes to user accounts are recorded too. Each event names the project as it was called at the time, so deleting a project keeps the record of who worked on it. Details hold counts and short before/after values (`client: Samana -> Emaar`); clause-by-clause compliance changes stay in `compliance_audit`.

The record is read back as one account: the user's details, the projects they created, are assigned to or opened, the submittals, BOQ revisions and compliance statements they made or changed, and their activity, filterable by project and kind.

- Everyone: click your name in the header (`/account`); API `GET /auth/me/account`, `/auth/me/activity`, `/auth/me/account/export.xlsx`.
- Admin, any user: **Users** -> the user's name (`/admin/users/{id}`); API `GET /users/{id}/account`, `/users/{id}/activity?action=boq&project_id=2&limit=100&offset=0`, `/users/{id}/account/export.xlsx`.

**Export to Excel** writes the whole record as a workbook: Account, Projects, Activity, Submittals, BOQ revisions, Compliance. The record lives in each PC's own database, like projects; it starts empty when the feature is installed, and earlier work shows only where the platform already stored who did it (project creator, revision issuer, submittal history, compliance approvals and audit).

## Frontend (React + Vite + Tailwind)

```
cd frontend
npm install
npm run dev
```

Runs at http://localhost:5173, expects the backend at http://localhost:8000 (override with `VITE_API_BASE_URL`).

## Notes

- Roles: Admin, Design Manager, Design Engineer, Draftsman, Viewer.
- Auth uses a JWT in an httpOnly cookie (not localStorage) to reduce XSS token theft. `samesite=lax` assumes frontend and API share a site in production â€” revisit if they end up on different domains.
- SQLite for dev (`DATABASE_URL` in `.env`); swap to Postgres for production via the same `DATABASE_URL`.
- Brand assets (logo, hero photo, exact colors) in `frontend/src/branding.ts`, `BrandMark.tsx`, `CitySkylineBackdrop.tsx` are placeholders reconstructed from the mockups â€” swap for the approved files when available.
- DRF extraction (`app/services/drf_extractor.py`) is OCR + grid-line detection, not an AI model â€” by design, per the plan's "AI handles document understanding, Python handles deterministic logic" split, this counts as deterministic since it's pattern/geometry matching against a known fixed form template, not a language model. It targets the current DRF template (Document Reference SSD-P-06 B/IQF.17); a template redesign would need it revisited. Extracted values are always shown as editable, pre-filled suggestions with a confidence indicator â€” never auto-submitted.

### What the log reads, and what it refuses to read

The register is built by reading every PDF in the project folder, and four
things about that read were wrong against the real archive (EP-30784):

- **Files over the Windows path limit were silently skipped.** `Path.is_file()`
  answers *False* for a path of 260 characters or more rather than raising, so
  54 of that project's 287 PDFs were never opened -- and being the deepest
  paths, they were almost exactly the shop drawings and consultant replies the
  log exists to track. Paths are now taken through the `\\?\` prefix
  (`_os_path`), which also means MuPDF cannot open them itself, so such a file
  is read here and handed over as bytes.
- **A reply to the consultant's comments registered itself as the submittal it
  answers.** The reply quotes the reference it replies to ("Ref No :
  BBY006-GME-MAS-EL-LI-0001 - R.00"), which was enough to file it as that
  submittal and displace the Materials Submittal Form of the same number. A
  reply is now evidence attached to a submission, never an entry: it is read
  only for a consultant decision it may carry, and dropped. A reply on its own
  is not an approval -- the contractor answering comments says nothing about
  the outcome.
- **Drawing titles read as "SCALE" and no drawing had a floor.** A CAD title
  block exports its labels and its values as two separate text runs, so the
  line after the "DRAWING TITLE" label is the next *label*. The values sit
  together after the drawing reference instead, and are now read positionally
  from it (`title_block`) -- which is what recovers the floor.
- **Revisions came from the general notes.** "REV" is likewise followed by
  whatever the export put next, on these sheets the notes, whose "1.)" read as
  revision 1. The revision of a shop drawing now comes from its submission
  folder (`.../1.FAVE/R1/05. Ground Floor/...`), because contractors leave the
  sheet's own revision at 00 across resubmissions -- on EP-30784 the R0 and R1
  submissions of every FAVE drawing both say 00, so the folder is the only
  thing separating them. Used for the revision only: a folder still never
  establishes approval.

A submission read off several pages is **one row**: a shop drawing arrives as a
form page plus the sheet, and the two carry different halves of one fact (the
form has the reference and the stamp, the sheet has the title and the floor).
Keeping whichever page ranked higher threw the other half away.

**A file that cannot be read is reported, not passed over.** A OneDrive file
that is still online-only says so and names the fix -- make the folder
available offline -- rather than being called corrupt.

**The page shows nothing until the scan finishes**, behind a progress bar. A
half-read scan shows a drawing with no later revision because that file has
not been opened yet, and a submittal as UR because the page carrying the
stamp is still ahead of it. Both read as fact and neither is one.

The two tables follow the platform owner's design and differ on purpose.
**Drawings** are one row per floor with a column per revision -- what is asked
of them is "which floors are issued, and where has each got to". **Material
Submittals** are one row per reference showing the latest revision, its status
and the earlier ones as a history trail (`R0 Rejected -> R1 ANN`): a submittal
has no floor and its older revisions are superseded, so a column each would be
mostly empty. A reference is grouped on its own, not per system code, so one
document read under two codes is still listed once.

### Project logs

Logs includes ALL and per-system views with revision history, search, status filters, file links and CSV export. Edwards FAS, VE and Fire Telephone share FAS; monitored self-contained lighting shares EML. Full Package scope includes Fire Rated Cable with Material Submittals only. Material Submittals includes prepared documents, defaulting to R0 / UR until a consultant decision is found. The Python scanner reads PDF forms, title blocks and drawing schedules, using OCR for scanned pages and stamps. Replies match by reference and revision. Drawing schedules supply floor rows; unrelated files in drawing folders are excluded. Explicit consultant decisions establish Approved, ANN or Rejected; unticked options do not. Refresh detects modified files, while unchanged file content is cached. Scanning runs in the background; the Logs page polls progress and displays results as documents are checked, keeping API requests responsive. OCR failures and scan limits are reported for verification. CAD-only files require a readable PDF submission or schedule. Technical Queries is removed from navigation.
