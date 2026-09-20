# EP directory improvement — Stage 1: database structure

Stage 1 adds the storage needed for fast EP lookups to the original platform. The first scanner, database-backed Find Project operation and automatic change detection are later stages, so the current search behavior is not switched yet.

## The full change, one stage at a time

1. Database structure: this stage.
2. First archive scan: discover every qualifying project folder beneath the configured root and save the directory. Preserve the existing bounded traversal and stop at project folders. Review the scan summary and duplicate EP locations.
3. Fast lookup: make Find Project query the directory while retaining the existing folder picker and document discovery. Handle an index not yet ready explicitly.
4. Background synchronization: detect new, moved and removed folders without making user searches wait for an archive walk. Start with configurable periodic directory scans; show scan failures and freshness. Do not infer deletion from an incomplete scan.
5. End-to-end verification: demonstrate that a new folder appears after sync and that searches do not walk the archive.

## Why two new tables?

`ep_archive_roots` remembers the configured archive and the status of its scan. `ep_archive_folders` stores one row per discovered project-folder location. The existing `projects` table still holds projects an engineer has actually created in the application.

Example, using fictional locations:

```text
Archive root: C:/Demo/Projects
  Client A/EP-29495 Training Tower
  Client B/EP-29495 Training Tower
```

This becomes one root record and two folder records. Finding EP 29495 must return both locations, so the existing choice-of-folder workflow can continue.

## Code to read first

The two appended model classes in `backend/app/models.py` are:

```python
class EpArchiveRoot(Base):
    __tablename__ = "ep_archive_roots"

class EpArchiveFolder(Base):
    __tablename__ = "ep_archive_folders"
```

These are SQLAlchemy models: Python descriptions of database tables. `mapped_column(...)` describes one column, such as its data type and whether it can be empty.

The root's `folders` relationship and the folder's `archive` relationship connect the two Python objects. The `archive_id` foreign key enforces the same link in the database.

## Root fields

| Field | Meaning |
|---|---|
| id | Internal database identifier |
| root_path | Full configured archive location |
| root_key | Fixed-length identity derived from the normalized root path |
| scan_status | pending, scanning, ready or failed; initially pending |
| scan_token | Identifier for a scan attempt |
| scan_started_at / scan_finished_at | When the current/latest attempt started and ended |
| last_successful_scan_at | Last complete successful scan; separate from a failed attempt |
| last_error | Reason a scan failed |
| created_at | When this root was first recorded |

## Folder fields

| Field | Meaning |
|---|---|
| id | Internal directory-entry identifier |
| archive_id | Which root this folder belongs to |
| ep_number | Normalized number, e.g. 29495; same convention as the existing platform |
| folder_name | The folder's visible name |
| relative_path | Its path below the archive root |
| path_key | Fixed-length identity derived from the normalized relative path |
| is_available | Whether the location is available in the most recent trustworthy view |
| first_seen_at / last_seen_at | First and most recent successful observation |
| last_seen_scan_token | Which scan most recently saw the folder |

The path keys are SHA-256 hashes used for short unique keys, not encryption. The next stage will implement path normalization and hashing. Full locations can be reconstructed from the archive's root path and the folder's relative path. Unlike a cloud item ID, path identity changes on a rename: the scanner will reconcile old and new locations after a complete scan.

## Two important database rules

```python
UniqueConstraint("archive_id", "path_key", name="uq_ep_archive_folder_path")
Index("ix_ep_archive_folders_lookup", "archive_id", "ep_number", "is_available")
```

- The unique constraint prevents the same folder being added twice to the same archive. It does not make EP numbers unique.
- The SQL index lets the database efficiently find available locations for an EP number within an archive.

No archive record or folder is populated by defining these models. The future scanner supplies the records. Merely indexing a directory will not create an application Project.

## What the migration does

`backend/alembic/versions/a8c2e7f4b109_ep_archive_directory.py` creates the two new tables and the lookup index. It follows the current migration head, `c4d5e6f7a8b9`.

A model describes a table in Python; a migration creates that table in an existing database. Both are needed. The application's existing startup migration runner will apply this migration when the backend next starts. The live application database was not manually migrated as part of this stage.

## Verification

The stage was tested on an isolated copy of the current working source and a disposable test database. Existing uncommitted project changes were included in that copy.

32 focused checks passed: the existing migration/model comparison and EP resolver tests, plus six new tests covering duplicate EP locations, duplicate folder rejection, separation between archives, unavailable history, invalid foreign keys/root duplication, and migration round-trip preservation of an existing project and the lookup index.

A pre-existing framework deprecation warning was reported. No archive documents were scanned and no AI calls were made by these checks.

## Your review point

Read `EpArchiveFolder` first. Explain these two lines in your own words:

```python
ep_number: Mapped[str] = mapped_column(String(32), nullable=False)
archive_id: Mapped[int] = mapped_column(ForeignKey("ep_archive_roots.id", ondelete="CASCADE"), nullable=False)
```

Question: why should two folders with the same EP number be allowed, while the same folder in the same archive should be prevented from appearing twice?

We pause after this stage so you can review or ask questions before the scanner is built.
