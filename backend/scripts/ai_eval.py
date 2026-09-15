"""Build the AI evaluation set from reviewed issues, and score a model on it.

    venv\\Scripts\\python scripts\\ai_eval.py build
    venv\\Scripts\\python scripts\\ai_eval.py run --limit 20
    venv\\Scripts\\python scripts\\ai_eval.py run --task read_cell --no-save
    venv\\Scripts\\python scripts\\ai_eval.py report

`build` calls no model: it adds every issue an engineer settled with a value
to backend/evaluations/cases/<task>.jsonl. `run` sends each case to the
configured provider (AI_PROVIDER, AI_MODEL_SMALL) -- this costs calls, so
`--limit` caps how many -- scores the answers and saves a report under
backend/evaluations/reports. `report` prints the latest report per task and
the task gates.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai import evaluation  # noqa: E402
from app.ai.provider import get_provider  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.migrations import upgrade_to_head  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="add reviewed issues to the evaluation set")
    build.add_argument("--task", default="read_cell")
    run = sub.add_parser("run", help="score the configured model on the evaluation set")
    run.add_argument("--task", default="read_cell")
    run.add_argument("--limit", type=int, default=None, help="at most this many cases")
    run.add_argument("--no-save", action="store_true", help="print the score without saving a report")
    sub.add_parser("report", help="the latest report per task and the gates")
    args = parser.parse_args()

    if args.command == "build":
        upgrade_to_head(engine)
        db = SessionLocal()
        try:
            cases = evaluation.cases_from_reviews(db, task=args.task)
        finally:
            db.close()
        path = evaluation.cases_path(args.task)
        evaluation.write_cases(cases, path)
        print(f"{len(cases)} reviewed {args.task} cases added or updated; {len(evaluation.load_cases(path))} in {path}")
        return 0

    if args.command == "run":
        path = evaluation.cases_path(args.task)
        if not path.exists():
            print(f"No cases at {path}: run `build` first.")
            return 1
        provider = get_provider()
        if not getattr(provider, "ready", False):
            print(f"The provider is not ready: {getattr(provider, 'status', '')}")
            return 1
        cases = evaluation.load_cases(path)[: args.limit]
        report = evaluation.run(cases, provider, task=args.task, model=get_settings().ai_model_small,
                                save=not args.no_save)
        print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
        wrong = [r for r in report["results"] if not r["correct"]]
        for r in wrong[:20]:
            print(f"  {r['id']}: expected {r['expected']!r}, proposed {r['proposed']!r} ({r['state']}) {r['error'] or ''}")
        return 0

    for row in evaluation.latest_reports():
        print(f"{row['task']}: {row['file']} prompt {row['prompt_version']} -> {json.dumps(row['score'])}")
        print(f"  gate: {row['gate']}")
    for gate in evaluation.gate_status():
        print(f"gate {gate['task']}: {'OPEN' if gate['open'] else 'closed'} "
              f"(needs {gate['min_cases']} cases, precision {gate['min_precision']}, "
              f"false validations <= {gate['max_false_validations']}; harness {'ready' if gate['runnable'] else 'not written'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
