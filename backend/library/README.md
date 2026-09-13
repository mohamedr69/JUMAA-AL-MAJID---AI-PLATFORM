# The company library

Everything a submittal needs that is **not about a particular project**: the
company's own documents, the templates a package is built from, and the
manufacturers' datasheets. The project archive holds the projects; this holds
the rest.

These documents used to be read out of the synced project archive. That was
wrong twice over. It was **slow** — indexing the 86 Edwards datasheets took
about eleven seconds on every start, because every directory entry in a
OneDrive tree is a cloud-placeholder lookup, and none of that work is about
the project being opened. And it was **not portable** — a path like
`Systems/01- FAVE/01- Edwards - UL&EN/01- EST4` is one company's filing on one
synced drive.

So it lives here, on local disk, in a fixed structure.

## The structure

```
library/
  datasheets/
    EDWARDS/                  one folder per manufacturer, named for the
    ROCKET/                   brand as the DRF and the BOQ spell it
    MENVIER/
  submittal/                  the submittal builder, section by section
    Company Profile/
    Product Catalogue or Brochure/
    Trade License/
    ISO Certificates/
    Civil defence certificates/
    Test certificates/
    Project Reference List/
    Previous Approvals/
    Country Of Origin/        COO.xlsx — where each model is made
    templates/                Cover Page - Material Submittal - R0.pdf
                              Index & divider.pdf
                              Draft Warranty.docx
                              not_applicable.pdf
                              will_be_submitted_separatelly.pdf
```

A manufacturer is **discovered, not configured**. Dropping a `MENVIER` folder
into `datasheets/` is all it takes for the platform to look parts up in it —
there is no list to edit and no restart to do. The same goes for widening the
battery range: a ROCKET datasheet added to `datasheets/ROCKET/` joins the
selection as soon as the library is reindexed.

The `submittal/` folder names come from the company's own
`Index & divider.pdf`, and `app/services/submittal_package.py` maps them to
section numbers. Renaming one here means renaming it there.

## Filling it

```
cd backend
.\venv\Scripts\python scripts\sync_library.py            # copy from the archive
.\venv\Scripts\python scripts\sync_library.py --status   # what is held now
```

The sync copies out of the archive named in `PROJECTS_ROOT` and never writes
back to it. Files whose size and timestamp already match are left alone, so
re-running it costs nothing.

Until a document is copied across, the platform **falls back to the archive**
for that document alone (`ARCHIVE_DATASHEET_LIBRARIES`,
`ARCHIVE_SUBMITTAL_LIBRARY`). A machine part-way through the move works; it is
just slower for what it has not got yet.

## Where it lives

`LIBRARY_ROOT` in `.env` overrides this folder — point it at a shared drive to
give a whole team one library. Unset, it is `backend/library`, resolved from
the code rather than from the working directory, so it does not matter where
`uvicorn` was started from.

The index is **not** kept in here. It goes under `CACHE_ROOT`
(`backend/.cache` by default), because a library folder can be read-only or
synced, and a cache written into a synced folder is uploaded for no reason.

## Git

The structure is tracked; the documents are not. A trade licence, ISO
certificates and test reports are the company's papers, and some of the
datasheet libraries run to hundreds of megabytes. `.gitignore` keeps the
folders and the `.gitkeep` files and ignores everything else, so a clone gets
the shape and fills it with `sync_library.py`.
