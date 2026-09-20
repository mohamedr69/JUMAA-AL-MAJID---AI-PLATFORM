# EP directory improvement — Stages 2 to 5: the scanner, the search box, and staying fresh

Stage 1 added the two tables. This stage fills them, reads from them, and keeps them current:

2. **First archive scan** — every project folder beneath the archive root, discovered and saved.
3. **Fast lookup** — Find Project asks the database, not OneDrive.
4. **Background synchronisation** — new, moved and removed folders noticed without anyone asking.
5. **The search box** — start typing and the EP number and project name are suggested.

## What changed, from the outside

Before, Create New Project asked for an EP number you already had to know, and every attempt walked the synced OneDrive archive looking for it.

Now the box suggests as you type:

```text
EP Number:  294
┌──────────────────────────────────────────────────┐
│ EP-29495 — IVY Garden 2                          │
│ Samana Developers/EP-29495 IVY Garden 2          │
│ [Already in the platform]                        │
├──────────────────────────────────────────────────┤
│ EP-29487 — One Sankari Luxury Residential Tower  │
│ Bilt Middle East/EP-29487 - One Sankari ...      │
└──────────────────────────────────────────────────┘
```

A project name works too: typing `wasl` finds EP-30851. Picking a suggestion goes straight to the review form; picking a number the platform already has offers to open that project rather than create it twice.

On the real archive the index holds **893 EP numbers in 1,174 folders**, and a rescan of the whole archive takes **about two seconds**.

## The one rule that makes the index everybody's

The archive is a SharePoint library that OneDrive syncs into each person's own profile:

```text
Mohamed's PC   C:\Users\moham\Juma Al Majid\SSD FIRE ALARM PROJECTS - ...\Samana Developers\EP-29495 IVY Garden 2
Ramadan's PC   C:\Users\mramadan\Juma Al Majid\SSD FIRE ALARM PROJECTS - ...\Samana Developers\EP-29495 IVY Garden 2
```

Those two paths are the same folder. So the database stores only the part that is the same:

```text
relative_path   Samana Developers/EP-29495 IVY Garden 2
```

Each PC joins that to its own `PROJECTS_ROOT`. No user's name is ever written into the index, and the index can be put in a shared `DATA_ROOT` folder for the whole team: one PC scans, every PC searches.

The archive is identified the same way — by the **library's folder name**, not by its path (`archive_identity`). Keyed on the path, a shared database would have held one full copy of the index per engineer, and the second person to open it would have re-indexed the whole archive.

## Code to read first

| File | What it is |
|---|---|
| `backend/app/services/ep_directory.py` | The whole feature: scan, search, lookup, status, background refresh |
| `backend/app/routers/archive.py` | `/archive/search`, `/archive/status`, `/archive/scan` |
| `backend/scripts/scan_archive.py` | The same scan from the command line |
| `frontend/src/components/EpNumberSearch.tsx` | The box with the dropdown |
| `frontend/src/pages/CreateProjectPage.tsx` | Where it is used, and the note under it |

## How a scan decides what is new, and what is gone

A scan walks the archive once, with the **same traversal the old search used**: the same depth limit, and the same rule that a project folder is never descended into — so `EP-30851 Al Wasl Tower/EP-99999 Cabinet Sample` is a sample cabinet inside a project, not a project.

Then it compares what it saw with what the index holds:

- a folder the index has never seen → **added** (a new EP number);
- a folder the index has → refreshed, keeping its `first_seen_at`;
- a folder the index has and the scan did not see → **only sometimes** missing.

That last one is the careful part.

### "Missing" is judged folder by folder, not archive by archive

Three folders in the real archive have paths past Windows' 260-character limit, so **every** scan fails to open them. If one unreadable folder made the whole scan untrustworthy, the index could never mark anything gone, and — worse, had it been written the other way round — a OneDrive folder that was merely busy would have marked half the archive missing.

So the scan records **which directories it managed to read**, and a stored folder is marked gone only when the directory that held it was read this time and no longer contained it:

```python
if key in found or not row.is_available:
    continue
if _parent_key(row.relative_path) not in walked:
    continue          # its parent was not read: unread is not gone
row.is_available = False
```

A row is never deleted. An EP number that moved between contractors keeps its history, and a folder that comes back becomes available again on the next scan.

## Find Project no longer walks the archive

`POST /projects/resolve` asks the index first:

- **the index has the number** → the folders come back with no walk at all;
- **the index holds this archive but not this number** → the folder may have been created since the last scan, so the archive *is* searched once, and whatever is found is written into the index (`observe`). That is the last time anyone waits for that walk;
- **the index holds nothing yet** → the archive is walked exactly as before.

Duplicate EP numbers are unchanged: two folders still means the "which folder is this?" picker.

## Staying fresh

| How | When |
|---|---|
| On server start | Unless `ARCHIVE_INDEX_SCAN_ON_START=false` |
| In the background | Every `ARCHIVE_INDEX_REFRESH_MINUTES` (default 30) |
| From the page | "Check for new projects" under the EP box |
| From the command line | `python -m scripts.scan_archive` |
| By accident, usefully | A Find Project that had to fall back to a walk records what it found |

The refresh is paced on the **last attempt**, not the last flawless one — otherwise the archive with its three unopenable folders would be walked again every single minute, for ever.

## Settings

All optional; the defaults are what the office wants.

```ini
ARCHIVE_INDEX_ENABLED=true          # false: every Find Project walks the archive, as before
ARCHIVE_INDEX_SCAN_ON_START=true    # nothing waits for it
ARCHIVE_INDEX_REFRESH_MINUTES=30
```

## The command-line script

```bash
python -m scripts.scan_archive              # scan, and list the new EP numbers
python -m scripts.scan_archive --dry-run    # what it would do, writing nothing
python -m scripts.scan_archive --status     # what is in the index now
python -m scripts.scan_archive --search 294 # what the search box would suggest
```

Useful for the first build on a new PC, or as a Windows scheduled task on the one PC that holds the shared data folder.

## What this does not do

- **It does not create projects.** A folder in the archive is a folder; a project is a record an engineer makes. Indexing 893 EP numbers created no projects.
- **It does not write to the archive.** The archive is read, and only read.
- **It does not read documents.** A scan looks at folder names. The DRF and the design sheets are still read only when a project is actually created.
- **It does not replace Microsoft Graph.** Production should still search SharePoint directly one day; the scan is the local stand-in, and the matching rules are the part that carries over.

## Verification

- 28 new tests in `backend/tests/test_ep_directory.py` — the scan, duplicate numbers, the same archive under a second user's profile, removal and return, an unreadable folder not freezing the index, the search box's ranking and prefix handling, the API's permissions, Find Project answering without a walk, and the refresh pacing.
- The whole backend suite, with this stage in place: **953 passed, 33 skipped, nothing failed** (15m40s).
- Against the **real archive**: 1,174 folders and 893 EP numbers indexed; a second scan reported 0 new and 0 missing; searches for `294` and `tower` returned real projects with their real names.
- Frontend typecheck and production build clean.
- One fix outside this stage, found by that build: the material submittal's Create button was written `onClick={create}`, which handed React's click event to `create` as its options argument. It now reads `onClick={() => void create()}`, like the two Rebuild buttons beside it. The build was failing on this before the stage began.

No archive documents were read and no AI calls were made.

## Your review point

Open the search box and type three digits of a number you know. Then answer this:

> The scan found a folder it had never seen, and did not find a folder it had seen last week. Why is the first one always recorded as new, while the second is only sometimes recorded as gone?
