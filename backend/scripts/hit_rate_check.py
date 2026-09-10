"""One-off diagnostic: discover real EP numbers in the archive and report how
often the deterministic resolver (app.services.ep_resolver) finds the
project folder, DRF, and Design Sheet for each. Not part of the test suite --
run manually against the real synced OneDrive tree.

Usage: venv\\Scripts\\python scripts\\hit_rate_check.py <root> [max_projects]
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ep_resolver import resolve_project  # noqa: E402

EP_NAME_RE = re.compile(r"^EP[-_ ]?(\d{4,6})", re.IGNORECASE)


def discover_ep_numbers(root: Path, max_depth: int = 4, limit: int = 60) -> list[str]:
    found: dict[str, None] = {}
    for dirpath, dirnames, _filenames in os.walk(root, onerror=lambda e: None):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= max_depth:
            dirnames[:] = []
            continue
        for name in dirnames:
            m = EP_NAME_RE.match(name)
            if m:
                found.setdefault(m.group(1), None)
        if len(found) >= limit:
            break
    return list(found.keys())[:limit]


def main() -> None:
    root = Path(sys.argv[1])
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    print(f"Discovering EP numbers under {root} (limit={limit})...")
    ep_numbers = discover_ep_numbers(root, limit=limit)
    print(f"Discovered {len(ep_numbers)} EP numbers: {ep_numbers}\n")

    folder_found = ambiguous = drf_found = ds_found = both_found = 0
    issues: list[str] = []

    for ep in ep_numbers:
        result = resolve_project(root, ep)
        if result.folder_not_found:
            issues.append(f"EP-{ep}: folder NOT FOUND")
            continue
        folder_found += 1
        if result.is_ambiguous:
            ambiguous += 1
            issues.append(f"EP-{ep}: AMBIGUOUS ({len(result.matched_folders)} matches)")
        has_drf = len(result.drf_candidates) > 0
        has_ds = len(result.design_sheet_candidates) > 0
        drf_found += has_drf
        ds_found += has_ds
        both_found += has_drf and has_ds
        if not has_drf:
            issues.append(f"EP-{ep}: no DRF ({result.matched_folders[0]})")
        if not has_ds:
            issues.append(f"EP-{ep}: no Design Sheet ({result.matched_folders[0]})")

    total = len(ep_numbers)
    print("=== Hit rate ===")
    print(f"Folder found:        {folder_found}/{total}")
    print(f"  of which ambiguous: {ambiguous}")
    print(f"DRF found:            {drf_found}/{total}")
    print(f"Design Sheet found:  {ds_found}/{total}")
    print(f"Both found:           {both_found}/{total}")
    print("\n=== Issues ===")
    for issue in issues:
        print(f" - {issue}")


if __name__ == "__main__":
    main()
