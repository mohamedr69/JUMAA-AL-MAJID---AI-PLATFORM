"""Review 02 A + C: legacy readings are kept, a bounded run carries the facts of the pages it did not visit,
the extraction profile is part of a reading's identity."""
import pathlib
B = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")

# ---------------------------------------------------------------- document_control: the profile
p = B / "app/services/document_control.py"; s = p.read_text(encoding="utf-8")
old = "def _promote_default() -> bool:"
new = '''def extraction_profile(promote: bool | None = None) -> str:
    """The extraction policy a reading was, or would be, made under: "promoted"
    (the evaluation path: every method and component becomes a record) or
    "default" (the compatibility path). Part of a reading's identity beside
    the parser version and the content hash (M2 review 02, C): a reading made
    under one profile is not the reading of the other, whatever the bytes."""
    if promote is None:
        promote = _promote_default()
    return "promoted" if promote else "default"


def _promote_default() -> bool:'''
assert old in s and "def extraction_profile" not in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

# ---------------------------------------------------------------- document_sync: the writer
p = B / "app/services/document_sync.py"; s = p.read_text(encoding="utf-8")
old = '''COMPLETE_OUTCOMES = ("complete", "bounded")
'''
new = '''COMPLETE_OUTCOMES = ("complete", "bounded")


def reading_to_keep(previous) -> dict | None:
    """The row's stored reading when it is one worth keeping in front of an
    attempt that did not complete: any reading with records -- the 367 live
    payloads read before parser versions existed included (M2 review 02, A1:
    missing provenance is unknown provenance, not absence of evidence) -- or
    a modern reading (a parser version or a content hash) even without
    records (an empty successful reading is a reading). A row holding only
    an earlier attempt and no reading is nothing to keep."""
    if not isinstance(previous, dict) or not isinstance(previous.get("records"), list):
        return None
    if previous["records"] or previous.get("parser_version") or previous.get("read_sha256"):
        return previous
    return None


def staleness(kept: dict, sha256: str | None):
    """Whether a kept reading describes other bytes than the file's now:
    True / False when the reading records the hash it was read from, None
    when it does not (a legacy reading: its source identity is unknown, and
    is said so rather than guessed)."""
    read_sha = kept.get("read_sha256")
    if not read_sha:
        return None
    return read_sha != sha256


CARRIED_UNVISITED = "carried_unvisited"
CARRIED_UNVERIFIED = "carried_unverified"


def carry_unvisited(kept: dict | None, coverage: dict | None, sha256: str | None) -> tuple[list[dict], str | None]:
    """The records of the kept reading that sit on pages the new, bounded
    reading did not visit -- carried into it, flagged, rather than dropped
    (M2 review 02, A2: a run that stopped at its page budget has not seen
    page 13 and cannot call its record absent). A record whose page is not
    known is carried too. When the kept reading was made from other bytes
    than the file's now (or from bytes unknown), the carried records are
    also flagged unverified: they are what the previous content held, not
    what the new content is known to hold. Returns (records, note)."""
    if kept is None or not coverage:
        return [], None
    unvisited = {int(p.get("page")) for p in (coverage.get("pages_skipped") or []) if p.get("page") is not None}
    if not unvisited:
        return [], None
    unverified = staleness(kept, sha256) is not False
    carried = []
    for record in kept.get("records") or []:
        if not isinstance(record, dict):
            continue
        page = record.get("page")
        if page is None or page in unvisited:
            flags = [f for f in (record.get("flags") or []) if f not in (CARRIED_UNVISITED, CARRIED_UNVERIFIED)]
            flags.append(CARRIED_UNVISITED)
            if unverified:
                flags.append(CARRIED_UNVERIFIED)
            carried.append({**record, "flags": flags})
    if not carried:
        return [], None
    pages = sorted({r.get("page") for r in carried if r.get("page") is not None})
    note = (f"{len(carried)} record{'s' if len(carried) != 1 else ''} on page{'s' if len(pages) != 1 else ''} "
            f"{', '.join(str(p) for p in pages) if pages else 'unknown'} not visited by this reading "
            f"{'were' if len(carried) != 1 else 'was'} kept from the previous reading"
            + ("; the file changed since, so they are unverified." if unverified else "."))
    return carried, note
'''
assert old in s and "def reading_to_keep" not in s; s = s.replace(old, new, 1)

old = '''    outcome = _outcome_of(coverage, notes)
    modified = datetime.fromtimestamp(stat.st_mtime_ns / 1e9, timezone.utc)
    previous = row.extracted if isinstance(row.extracted, dict) else None
    stored_records = [_record_dict(document_control.replace(r, path=path.relative_to(root).as_posix()), root) for r in records]
    if outcome not in COMPLETE_OUTCOMES:
        attempt = {"at": utc_now().isoformat(), "outcome": outcome, "sha256": row.sha256, "notes": list(notes),
                   "parser_version": document_control.PARSER_VERSION, "coverage": coverage,
                   "error": (coverage or {}).get("error"), "observations": list(observations or [])}
        if outcome == "partial":
            attempt["records"] = stored_records
        # A reading to keep: the last complete one, or the partial one that
        # stands in for it -- with an earlier attempt already beside it or not.
        complete_before = previous is not None and previous.get("records") is not None and bool(previous.get("parser_version"))
        if complete_before:
            # The last complete reading stands; the attempt is recorded beside it.
            extracted = dict(previous)
            extracted["attempt"] = attempt
            extracted["notes"] = list(notes)
            read_sha = previous.get("read_sha256")
            extracted["stale"] = bool(read_sha) and read_sha != row.sha256
        elif outcome == "partial":'''
new = '''    outcome = _outcome_of(coverage, notes)
    modified = datetime.fromtimestamp(stat.st_mtime_ns / 1e9, timezone.utc)
    previous = row.extracted if isinstance(row.extracted, dict) else None
    kept = reading_to_keep(previous)
    profile = (document_control.extraction_profile(bool(coverage.get("promoted"))) if coverage and "promoted" in coverage
               else document_control.extraction_profile())
    stored_records = [_record_dict(document_control.replace(r, path=path.relative_to(root).as_posix()), root) for r in records]
    if outcome not in COMPLETE_OUTCOMES:
        attempt = {"at": utc_now().isoformat(), "outcome": outcome, "sha256": row.sha256, "notes": list(notes),
                   "parser_version": document_control.PARSER_VERSION, "profile": profile, "coverage": coverage,
                   "error": (coverage or {}).get("error"), "observations": list(observations or [])}
        if outcome == "partial":
            attempt["records"] = stored_records
        # A reading to keep: the last complete one (modern or legacy), or the
        # partial one that stands in for it -- with an earlier attempt
        # already beside it or not. Its records, its form evidence, its
        # provenance (or the absence of one) stay exactly as they were.
        if kept is not None:
            extracted = dict(kept)
            extracted["attempt"] = attempt
            extracted["notes"] = list(notes)
            extracted["stale"] = staleness(kept, row.sha256)
            if extracted["stale"] is None:
                extracted["source_identity"] = "unknown"   # a legacy reading: no hash of what it was read from
            else:
                extracted.pop("source_identity", None)
        elif outcome == "partial":'''
assert old in s; s = s.replace(old, new, 1)

old = '''            extracted = {"records": stored_records, "notes": list(notes), "parser_version": document_control.PARSER_VERSION,
                         "read_sha256": row.sha256, "read_at": attempt["at"], "coverage": coverage,
                         "observations": list(observations or []), "attempt": {k: v for k, v in attempt.items() if k != "records"}}'''
new = '''            extracted = {"records": stored_records, "notes": list(notes), "parser_version": document_control.PARSER_VERSION,
                         "profile": profile, "read_sha256": row.sha256, "read_at": attempt["at"], "coverage": coverage,
                         "observations": list(observations or []), "attempt": {k: v for k, v in attempt.items() if k != "records"}}
            if previous is not None and "form" in previous:
                extracted["form"] = previous["form"]'''
assert old in s; s = s.replace(old, new, 1)

old = '''    extracted = {
        "records": stored_records,
        "notes": list(notes),
        # The parser the records were read with (document_control.PARSER_VERSION):
        # a reading under an earlier one is read again when the row is next
        # processed, whatever the file's hash.
        "parser_version": document_control.PARSER_VERSION,
        "read_sha256": row.sha256,
        "read_at": utc_now().isoformat(),
        "coverage": coverage,
        "observations": list(observations or []),
    }
    if evidence is not None:
        extracted["evidence"] = evidence
    if records:
        first = records[0]
        row.reference, row.revision, row.status = first.reference, first.revision, first.status
        row.system_code = row.system_code or first.system_code'''
new = '''    # A bounded reading (stopped at the reader's page budget) replaces the
    # kept one only with what it saw: the kept reading's records on the
    # pages it did not visit are carried, flagged, and said so in a note.
    carried, carried_note = ([], None) if outcome != "bounded" else carry_unvisited(kept, coverage, row.sha256)
    if carried:
        coverage = {**coverage, "carried_from_previous": sorted({r.get("page") for r in carried if r.get("page") is not None})}
        notes = (*notes, carried_note)
    extracted = {
        "records": stored_records + carried,
        "notes": list(notes),
        # The parser the records were read with (document_control.PARSER_VERSION)
        # and the extraction profile (document_control.extraction_profile): a
        # reading under an earlier parser, or under the other profile, is read
        # again when the row is next processed, whatever the file's hash.
        "parser_version": document_control.PARSER_VERSION,
        "profile": profile,
        "read_sha256": row.sha256,
        "read_at": utc_now().isoformat(),
        "coverage": coverage,
        "observations": list(observations or []),
    }
    if evidence is not None:
        extracted["evidence"] = evidence
    if previous is not None and "form" in previous:
        # The model's reading of the form is evidence of its own (M2 review
        # 02, A): it travels with the row until the model reads the form again.
        extracted["form"] = previous["form"]
    if records:
        first = records[0]
        row.reference, row.revision, row.status = first.reference, first.revision, first.status
        row.system_code = row.system_code or first.system_code
    elif carried:
        first_carried = carried[0]
        row.reference, row.revision, row.status = first_carried.get("reference"), first_carried.get("revision"), first_carried.get("status")'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

# ---------------------------------------------------------------- document_processing: freshness includes the profile
p = B / "app/services/document_processing.py"; s = p.read_text(encoding="utf-8")
old = '''    return (row.extracted or {}).get("parser_version") == document_control.PARSER_VERSION
'''
new = '''    extracted = row.extracted or {}
    return (extracted.get("parser_version") == document_control.PARSER_VERSION
            # ... and under the extraction profile in force now (M2 review 02, C):
            # an evaluation reading does not stand for the compatibility one,
            # nor the reverse, and a reading whose profile is not recorded is
            # not assumed to be either.
            and extracted.get("profile") == document_control.extraction_profile())
'''
assert old in s; s = s.replace(old, new, 1)
old = '''    """Whether the row's reading was made by the parser as it is now
    (document_control.PARSER_VERSION). A reading that was not is not kept
    for its hash: a known defect of the earlier parser would otherwise
    stand for ever behind an unchanged file."""'''
new = '''    """Whether the row's reading was made by the parser as it is now
    (document_control.PARSER_VERSION) and under the extraction profile in
    force (document_control.extraction_profile). A reading that was not is
    not kept for its hash: a known defect of the earlier parser, or the
    other profile's observations, would otherwise stand for ever behind an
    unchanged file."""'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

# ---------------------------------------------------------------- repair tool: selection and apply carry the profile
p = B / "scripts/repair_extraction.py"; s = p.read_text(encoding="utf-8")
old = '''        if "parser-outdated" in selections and (row.extracted or {}).get("parser_version") != document_control.PARSER_VERSION:
            reasons.append("read by an earlier parser")'''
new = '''        if "parser-outdated" in selections and (row.extracted or {}).get("parser_version") != document_control.PARSER_VERSION:
            reasons.append("read by an earlier parser")
        elif "parser-outdated" in selections and (row.extracted or {}).get("profile") != document_control.extraction_profile():
            reasons.append(f"read under another extraction profile ({(row.extracted or {}).get('profile') or 'not recorded'}, now {document_control.extraction_profile()})")'''
assert old in s; s = s.replace(old, new, 1)
old = '''    extracted["parser_version"] = document_control.PARSER_VERSION
    extracted["repaired_at"] = utc_now().isoformat()'''
new = '''    extracted["parser_version"] = document_control.PARSER_VERSION
    coverage = entry["file"].get("coverage") or {}
    extracted["profile"] = document_control.extraction_profile(bool(coverage.get("promoted"))) if "promoted" in coverage else document_control.extraction_profile()
    extracted["repaired_at"] = utc_now().isoformat()'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("A + C patched")
