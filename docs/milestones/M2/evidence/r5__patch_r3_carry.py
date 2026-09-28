"""Review 03, R3-01 + R3-02: a carried record keeps its own provenance (source hash, parser, profile, time -- or
unknown), its uncertainty follows that provenance and not the enclosing envelope, and its decision is projected only
when its source bytes and profile are the current ones; a reading that mixes profiles is not a current reading."""
import pathlib
B = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")

p = B / "app/services/document_sync.py"; s = p.read_text(encoding="utf-8")
i = s.index("CARRIED_UNVISITED = \"carried_unvisited\"")
j = s.index("def _outcome_of(")
new_block = '''CARRIED_UNVISITED = "carried_unvisited"
CARRIED_UNVERIFIED = "carried_unverified"
CARRIED_OTHER_PROFILE = "carried_other_profile"
CARRY_FLAGS = (CARRIED_UNVISITED, CARRIED_UNVERIFIED, CARRIED_OTHER_PROFILE)
RETAINED_METHOD = "retained"


def retained_provenance(record: dict, kept: dict) -> dict:
    """Where a carried record was read: its own retained provenance when it
    was carried before (it travels with the record and is never restamped
    by a later envelope -- M2 review 03, R3-01), else the kept reading's
    envelope, with None for what that reading did not record (a legacy
    reading: unknown, never invented)."""
    own = record.get("retained")
    if isinstance(own, dict) and "source_sha256" in own:
        return dict(own)
    return {"source_sha256": kept.get("read_sha256"), "parser_version": kept.get("parser_version"),
            "profile": kept.get("profile"), "read_at": kept.get("read_at")}


def carry_unvisited(kept: dict | None, coverage: dict | None, sha256: str | None, profile: str | None = None) -> tuple[list[dict], str | None]:
    """The records of the kept reading that sit on pages the new, bounded
    reading did not visit -- carried into it, flagged, rather than dropped
    (M2 review 02, A2: a run that stopped at its page budget has not seen
    page 13 and cannot call its record absent). A record whose page is not
    known is carried too.

    Each carried record keeps its own provenance (`retained`: the content
    hash, parser, profile and time it was read under, None where unknown)
    and its flags follow that provenance, not the new envelope (M2 review
    03): `carried_unverified` when its source bytes are not the file's now
    (or unknown), `carried_other_profile` when it was read under another
    profile than `profile` (or an unknown one). A retained record's decision
    is projected as its status only when both its bytes and its profile are
    the current ones; otherwise the status is UR and the decision is kept
    as a candidate (method "retained"), so a consumer never takes an
    unverified or an evaluation-profile decision for a current one. A
    record carried again later under matching bytes and profile gets its
    decision back. Returns (records, note)."""
    if kept is None or not coverage:
        return [], None
    unvisited = {int(p.get("page")) for p in (coverage.get("pages_skipped") or []) if p.get("page") is not None}
    if not unvisited:
        return [], None
    carried = []
    for record in kept.get("records") or []:
        if not isinstance(record, dict):
            continue
        page = record.get("page")
        if page is not None and page not in unvisited:
            continue
        retained = retained_provenance(record, kept)
        unverified = retained["source_sha256"] is None or retained["source_sha256"] != sha256
        other_profile = profile is not None and (retained["profile"] is None or retained["profile"] != profile)
        flags = [f for f in (record.get("flags") or []) if f not in CARRY_FLAGS] + [CARRIED_UNVISITED]
        if unverified:
            flags.append(CARRIED_UNVERIFIED)
        if other_profile:
            flags.append(CARRIED_OTHER_PROFILE)
        new = {**record, "flags": flags, "retained": retained}
        candidates = [list(c) for c in (record.get("decision_candidates") or [])]
        held = next((c for c in candidates if len(c) == 3 and c[2] == RETAINED_METHOD), None)
        if unverified or other_profile:
            status = record.get("status")
            if status not in (None, "UR") and held is None:
                why = f"retained from a {retained['profile'] or 'unknown-profile'} reading of " + ("other bytes" if unverified else "these bytes")
                candidates.append([status, why, RETAINED_METHOD])
            new["status"] = "UR"
            new["decision_candidates"] = candidates
        elif held is not None:
            # its bytes and profile are the current ones again: the decision it carried is its status
            new["status"] = held[0]
            new["decision_candidates"] = [c for c in candidates if c is not held]
        carried.append(new)
    if not carried:
        return [], None
    pages = sorted({r.get("page") for r in carried if r.get("page") is not None})
    unverified_n = sum(1 for r in carried if CARRIED_UNVERIFIED in r["flags"]); other_n = sum(1 for r in carried if CARRIED_OTHER_PROFILE in r["flags"])
    note = (f"{len(carried)} record{'s' if len(carried) != 1 else ''} on page{'s' if len(pages) != 1 else ''} "
            f"{', '.join(str(p) for p in pages) if pages else 'unknown'} not visited by this reading "
            f"{'were' if len(carried) != 1 else 'was'} kept from the previous reading"
            + (f"; {unverified_n} read from other or unknown bytes (unverified)" if unverified_n else "")
            + (f"; {other_n} read under another or unknown extraction profile (decision held)" if other_n else "") + ".")
    return carried, note


def retained_summary(records: list[dict]) -> dict | None:
    """What a reading retains from earlier readings, for consumers: how
    many records, from which sources, and whether any is unverified or of
    another profile (a reading with such records is not a current reading
    of its profile: document_processing.parser_current)."""
    kept = [r for r in records if isinstance(r, dict) and CARRIED_UNVISITED in (r.get("flags") or [])]
    if not kept:
        return None
    return {"records": len(kept), "pages": sorted({r.get("page") for r in kept if r.get("page") is not None}),
            "unverified": sum(1 for r in kept if CARRIED_UNVERIFIED in r["flags"]),
            "other_profile": sum(1 for r in kept if CARRIED_OTHER_PROFILE in r["flags"]),
            "sources": sorted({json.dumps({k: (r.get("retained") or {}).get(k) for k in ("source_sha256", "parser_version", "profile")}, sort_keys=True) for r in kept})}


'''
s = s[:i] + new_block + s[j:]
if "\nimport json" not in s[:3000]:
    s = s.replace("import re\n", "import json\nimport re\n", 1)
old = '''    carried, carried_note = ([], None) if outcome != "bounded" else carry_unvisited(kept, coverage, row.sha256)'''
new = '''    carried, carried_note = ([], None) if outcome != "bounded" else carry_unvisited(kept, coverage, row.sha256, profile)'''
assert old in s; s = s.replace(old, new, 1)
old = '''    if evidence is not None:
        extracted["evidence"] = evidence
    if previous is not None and "form" in previous:
        # The model's reading of the form is evidence of its own (M2 review
        # 02, A): it travels with the row until the model reads the form again.
        extracted["form"] = previous["form"]'''
new = '''    if evidence is not None:
        extracted["evidence"] = evidence
    retained = retained_summary(extracted["records"])
    if retained is not None:
        extracted["retained"] = retained
    if previous is not None and "form" in previous:
        # The model's reading of the form is evidence of its own (M2 review
        # 02, A): it travels with the row until the model reads the form again.
        extracted["form"] = previous["form"]'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

# ---------------------------------------------------------------- freshness: a mixed reading is not current; not a duplicate source
p = B / "app/services/document_processing.py"; s = p.read_text(encoding="utf-8")
old = '''    extracted = row.extracted or {}
    return (extracted.get("parser_version") == document_control.PARSER_VERSION
            # ... and under the extraction profile in force now (M2 review 02, C):
            # an evaluation reading does not stand for the compatibility one,
            # nor the reverse, and a reading whose profile is not recorded is
            # not assumed to be either.
            and extracted.get("profile") == document_control.extraction_profile())'''
new = '''    extracted = row.extracted or {}
    retained = extracted.get("retained") or {}
    return (extracted.get("parser_version") == document_control.PARSER_VERSION
            # ... and under the extraction profile in force now (M2 review 02, C):
            # an evaluation reading does not stand for the compatibility one,
            # nor the reverse, and a reading whose profile is not recorded is
            # not assumed to be either ...
            and extracted.get("profile") == document_control.extraction_profile()
            # ... and not carrying records read under another or an unknown
            # profile (M2 review 03, R3-02): such a reading is a mixed one,
            # never reused as this profile's; it is read again when the row
            # is next processed or repaired (not on its own: no retry loop).
            and not retained.get("other_profile"))'''
assert old in s; s = s.replace(old, new, 1)
old = '''             if r.error is None and not document_sync._was_unavailable(r) and parser_current(r)}'''
new = '''             if r.error is None and not document_sync._was_unavailable(r) and parser_current(r)
             # a reading that retains records of an earlier reading of its own file is that
             # file's history, not the content's: never copied to another file (M2 review 03)
             and not (r.extracted or {}).get("retained")}'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

# ---------------------------------------------------------------- repair tool: the same carry, the same selection
p = B / "scripts/repair_extraction.py"; s = p.read_text(encoding="utf-8")
old = '''        carried, carried_note = document_sync.carry_unvisited(document_sync.reading_to_keep(row.extracted), coverage, row.sha256)'''
new = '''        carried, carried_note = document_sync.carry_unvisited(document_sync.reading_to_keep(row.extracted), coverage, row.sha256,
                                                             document_control.extraction_profile(bool(coverage.get("promoted"))) if "promoted" in coverage else document_control.extraction_profile())'''
assert old in s; s = s.replace(old, new, 1)
old = '''        elif "parser-outdated" in selections and (row.extracted or {}).get("profile") != document_control.extraction_profile():
            reasons.append(f"read under another extraction profile ({(row.extracted or {}).get('profile') or 'not recorded'}, now {document_control.extraction_profile()})")'''
new = '''        elif "parser-outdated" in selections and (row.extracted or {}).get("profile") != document_control.extraction_profile():
            reasons.append(f"read under another extraction profile ({(row.extracted or {}).get('profile') or 'not recorded'}, now {document_control.extraction_profile()})")
        elif "parser-outdated" in selections and ((row.extracted or {}).get("retained") or {}).get("other_profile"):
            reasons.append("carries records read under another or an unknown extraction profile")'''
assert old in s; s = s.replace(old, new, 1)
old = '''    extracted["coverage"] = entry["file"].get("coverage")
    extracted["observations"] = entry["file"].get("observations") or []
    extracted.pop("attempt", None)
    extracted.pop("stale", None)'''
new = '''    extracted["coverage"] = entry["file"].get("coverage")
    extracted["observations"] = entry["file"].get("observations") or []
    extracted.pop("attempt", None)
    extracted.pop("stale", None)
    retained = document_sync.retained_summary(extracted["records"])
    if retained is not None:
        extracted["retained"] = retained
    else:
        extracted.pop("retained", None)'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("R3-01/02 patched")
