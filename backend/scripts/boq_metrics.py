"""BOQ extraction against the manually verified Golden Truth of a project.

    venv\\Scripts\\python -m scripts.boq_metrics --project 1 --golden tests/fixtures/boq_ep30784_golden_v1.json
    venv\\Scripts\\python -m scripts.boq_metrics --project 1 --golden ... --read --out bench/boq-phaseN.json

Two things can be measured: the BOQ the platform holds for the project
(what the engineer sees: `--project` alone), or a fresh read of the
project's Design Sheets the way the platform reads them (`--read`: the
stored reading resumed, nothing written to the BOQ), reported as the
accepted lines and the review rows the read produced.

The metrics are reported apart, never as one overall figure:

  row detection recall / precision   golden rows found among lines+review /
                                     lines+review that are golden rows
  part number accuracy               of matched rows, the part read right
  quantity accuracy                  of matched rows, the quantity read right
  part+quantity pair accuracy        both right
  group accuracy                     the group heading right
  review rate                        review rows / detected rows
  false auto-accept rate             accepted lines whose part or quantity is
                                     wrong / accepted lines  (the safety metric)
  false removal rate                 golden rows neither a line nor a review row
                                     / golden rows (a read; for a BOQ: lines
                                     missing from it)
  AI verification rate, timeout rate, calls per page, latency (a read)

A golden row is matched to an extracted row by its normalised part number
(the same part quoted twice -- 4-CPU in two panels -- by page order, the
nearest group), or, for a row with no part number, by the start of its
description.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _part(text) -> str:
    return _norm(text)


def _desc_key(text) -> str:
    return _norm(text)[:24]


def _quantity_key(text) -> str:
    text = str(text or "").strip()
    return text.replace(",", "").lstrip("0") or ("0" if text else "")


def match_rows(golden: list[dict], extracted: list[dict]) -> list[tuple[dict, dict | None]]:
    """(golden row, the extracted row it is, or None), greedy in page order:
    the same part number (or, without one, the same description start),
    the same page and group preferred."""
    free = list(range(len(extracted)))
    pairs: list[tuple[dict, dict | None]] = []
    for gold in golden:
        gold_part = _part(gold.get("part_number"))
        gold_desc = _desc_key(gold.get("description"))
        best, best_score = None, -1
        for index in free:
            row = extracted[index]
            part = _part(row.get("part_number"))
            if gold_part:
                if part != gold_part:
                    continue
                score = 10
            else:
                if part or _desc_key(row.get("description")) != gold_desc:
                    continue
                score = 10
            if row.get("page") == gold.get("page"):
                score += 3
            if _norm(row.get("group")) == _norm(gold.get("group")):
                score += 2
            if _desc_key(row.get("description")) == gold_desc:
                score += 1
            if score > best_score:
                best, best_score = index, score
        if best is None:
            pairs.append((gold, None))
        else:
            free.remove(best)
            pairs.append((gold, extracted[best]))
    for index in free:
        pairs.append((None, extracted[index]))  # type: ignore[arg-type]
    return pairs


def metrics(golden: list[dict], lines: list[dict], review: list[dict], *, run_info: dict | None = None) -> dict:
    """The metrics of section 26 for one system's rows. `lines` are the
    accepted rows, `review` the rows for the engineer; each is
    {"part_number", "description", "quantity", "group", "page"} (a review
    row's quantity is its primary reading)."""
    detected = [dict(r, _kind="line") for r in lines] + [dict(r, _kind="review") for r in review]
    pairs = match_rows(golden, detected)
    matched = [(g, e) for g, e in pairs if g is not None and e is not None]
    missing = [g for g, e in pairs if g is not None and e is None]
    extra = [e for g, e in pairs if g is None]
    lines_matched = [(g, e) for g, e in matched if e["_kind"] == "line"]
    part_right = sum(1 for g, e in matched if _part(g.get("part_number")) == _part(e.get("part_number")))
    qty_right = sum(1 for g, e in matched if _quantity_key(g.get("quantity")) == _quantity_key(e.get("quantity")))
    pair_right = sum(1 for g, e in matched if _part(g.get("part_number")) == _part(e.get("part_number"))
                     and _quantity_key(g.get("quantity")) == _quantity_key(e.get("quantity")))
    group_right = sum(1 for g, e in matched if _norm(g.get("group")) == _norm(e.get("group")))
    false_accepts = [(g, e) for g, e in lines_matched
                     if _part(g.get("part_number")) != _part(e.get("part_number"))
                     or _quantity_key(g.get("quantity")) != _quantity_key(e.get("quantity"))]
    extra_lines = [e for e in extra if e["_kind"] == "line"]

    def rate(a, b):
        return round(a / b, 4) if b else None

    out = {
        "golden_rows": len(golden), "lines": len(lines), "review_rows": len(review),
        "row_detection_recall": rate(len(matched), len(golden)),
        "row_detection_precision": rate(len(matched), len(detected)),
        "part_number_accuracy": rate(part_right, len(matched)),
        "quantity_accuracy": rate(qty_right, len(matched)),
        "pair_accuracy": rate(pair_right, len(matched)),
        "group_accuracy": rate(group_right, len(matched)),
        "review_rate": rate(len(review), len(detected)),
        "false_auto_accept_rate": rate(len(false_accepts) + len(extra_lines), len(lines)),
        "false_auto_accepts": [{"golden": {k: g.get(k) for k in ("part_number", "quantity", "group")} if g else None,
                                "line": {k: e.get(k) for k in ("part_number", "quantity", "group", "description")}}
                               for g, e in false_accepts] + [{"golden": None, "line": {k: e.get(k) for k in ("part_number", "quantity", "group", "description")}}
                                                             for e in extra_lines],
        "false_removal_rate": rate(len(missing), len(golden)),
        "missing_rows": [{k: g.get(k) for k in ("page", "part_number", "quantity", "description")} for g in missing],
        "extra_review_rows": [{k: e.get(k) for k in ("page", "part_number", "quantity", "description")} for e in extra if e["_kind"] == "review"],
        "wrong_quantity": [{"part_number": g.get("part_number"), "golden": g.get("quantity"), "read": e.get("quantity"), "kind": e["_kind"]}
                           for g, e in matched if _quantity_key(g.get("quantity")) != _quantity_key(e.get("quantity"))],
        "wrong_group": [{"part_number": g.get("part_number"), "golden": g.get("group"), "read": e.get("group")}
                        for g, e in matched if _norm(g.get("group")) != _norm(e.get("group"))],
    }
    if run_info:
        out.update(run_info)
    return out


def _from_boq(db, project_id: int, system: str) -> tuple[list[dict], list[dict]]:
    from app.models import ProjectBoqItem

    lines = [{"part_number": i.catalog_no, "description": i.description, "quantity": i.quantity, "group": i.group_heading,
              "page": i.source_page}
             for i in db.query(ProjectBoqItem).filter(ProjectBoqItem.project_id == project_id, ProjectBoqItem.system_code == system)
             .order_by(ProjectBoqItem.position)]
    return lines, []


def _from_read(db, project, sheet) -> tuple[list[dict], list[dict], dict]:
    from app.ai import sheet_reader
    from app.models import AiUsage

    before = db.query(AiUsage.id).count()
    started = time.perf_counter()
    result = sheet_reader.read_design_sheet(db, project, sheet)
    wall = time.perf_counter() - started
    rows = db.query(AiUsage).filter(AiUsage.id > before).all()
    lines = [{"part_number": l.catalog_no, "description": l.description, "quantity": l.quantity, "group": l.group_heading, "page": l.page,
              "evidence": l.evidence} for l in result.lines]
    review = []
    for issue in result.issues:
        if not issue.target.startswith("boq_line:"):
            continue
        d = issue.detail or {}
        review.append({"part_number": d.get("catalog_no"), "description": d.get("description"),
                       "quantity": (d.get("primary") or {}).get("quantity") or (d.get("ai_reading") or {}).get("quantity"),
                       "group": d.get("group_heading"), "page": issue.page, "reason_code": d.get("reason_code")})
    pages = max(1, sum(1 for p in result.coverage.pages))
    calls = [r for r in rows if not r.cache_hit]
    info = {
        "state": result.state, "budget_exhausted": result.budget_exhausted, "wall_s": round(wall, 1),
        "ai_calls": len(calls), "ai_calls_per_page": round(len(calls) / pages, 2), "cache_hits": len(rows) - len(calls),
        "ai_timeouts": sum(1 for r in calls if r.outcome == "timeout"),
        "ai_timeout_rate": round(sum(1 for r in calls if r.outcome == "timeout") / len(calls), 4) if calls else 0.0,
        "ai_verification_rate": round(sum(1 for r in review if r.get("reason_code")) / max(1, len(lines) + len(review)), 4),
        "calls_by_task": dict(Counter(f"{r.task}|{r.outcome}" for r in calls)),
        "latency_s_by_task": {t: round(sum(r.latency_ms for r in calls if r.task == t) / 1000 / max(1, sum(1 for r in calls if r.task == t)), 1)
                              for t in {r.task for r in calls}},
        "review_reasons": dict(Counter(r.get("reason_code") for r in review)),
        "evidence_levels": dict(Counter((l.get("evidence") or {}).get("level", "none") for l in lines)),
        "evidence_components": dict(Counter(name for l in lines for name, on in ((l.get("evidence") or {}).get("components") or {}).items() if on)),
        "notes": list(result.notes),
    }
    return lines, review, info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=int, required=True)
    parser.add_argument("--golden", required=True)
    parser.add_argument("--read", action="store_true", help="read the sheets now (the stored reading resumed) instead of the BOQ held")
    parser.add_argument("--out")
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.database import SessionLocal
    from app.models import Project

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    rows = golden["rows"]
    db = SessionLocal()
    project = db.get(Project, args.project)
    report = {"label": args.label, "project": project.ep_number, "source": "read" if args.read else "boq", "systems": {}}
    systems = sorted({r["system_code"] for r in rows})
    for system in systems:
        gold = [r for r in rows if r["system_code"] == system]
        info: dict = {}
        if args.read:
            sheet = next((s for s in project.design_sheets if (s.system_code or "").upper() in (system, {"EML": "ELS"}.get(system, system))), None)
            if sheet is None:
                report["systems"][system] = {"error": "no design sheet for this system"}
                continue
            lines, review, info = _from_read(db, project, sheet)
        else:
            code = next((s.system_code for s in project.design_sheets if (s.system_code or "").upper() in (system, {"EML": "ELS"}.get(system, system))), system)
            lines, review = _from_boq(db, args.project, code)
        report["systems"][system] = metrics(gold, lines, review, run_info=info)
    for system, m in report["systems"].items():
        print(f"== {system}: golden {m.get('golden_rows')} | lines {m.get('lines')} review {m.get('review_rows')}")
        for key in ("row_detection_recall", "row_detection_precision", "part_number_accuracy", "quantity_accuracy",
                    "pair_accuracy", "group_accuracy", "review_rate", "false_auto_accept_rate", "false_removal_rate",
                    "state", "wall_s", "ai_calls", "ai_calls_per_page", "ai_timeouts", "review_reasons",
                    "evidence_levels", "evidence_components"):
            if key in m:
                print(f"   {key}: {m[key]}")
        for key in ("false_auto_accepts", "missing_rows", "wrong_quantity", "wrong_group"):
            if m.get(key):
                print(f"   {key}:")
                for entry in m[key][:15]:
                    print("      ", entry)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print("written", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
