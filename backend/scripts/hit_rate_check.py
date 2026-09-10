"""One-off diagnostic: sample real EP folders across many different client
subtrees (not just wherever a plain walk lands first) and report two
separate numbers:

  1. Retrieval: given only an EP number, does find_ep_folders(root, ep) from
     the archive root return the intended folder among its matches, and how
     often is the result ambiguous (>1 match)?
  2. Classification: given the *correct* folder directly, how often does it
     contain a DRF / Design Sheet in the shape find_drf_candidates /
     find_design_sheet_candidates expect?

Keeping these separate matters: a low number on (1) is a resolver/data
problem (which folder is the right one), a low number on (2) is a
convention problem (this folder just doesn't have the documents in the
newer Scan Document / Commercial Document shape). Conflating them (as the
first version of this script did) produces a misleading headline number.

Not part of the test suite -- run manually against the real synced OneDrive
tree. Usage: venv\\Scripts\\python scripts\\hit_rate_check.py <root> [clients] [per_client]
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ep_resolver import (  # noqa: E402
    ANY_EP_FOLDER_RE,
    find_design_sheet_candidates,
    find_drf_candidates,
    find_ep_folders,
)

EP_NAME_RE = re.compile(r"^EP[-_ ]?(\d{4,6})", re.IGNORECASE)

# Excludes EP-numbered *utility* subfolders (a commercial-docs folder, a
# cause-and-effect working folder, an ELV input folder, ...) from the sample
# -- they aren't project roots, can't contain a "Scan Document" or
# "Commercial" child of their own, and would understate the classification
# hit rate if counted as "no DRF" / "no Design Sheet".
UTILITY_SUFFIX_RE = re.compile(
    r"(commercial|commerical|scan|documents?|c\s*&\s*e|\bms\b|elv input|elv-?\s*input)",
    re.IGNORECASE,
)


def sample_ep_folders(root: Path, max_clients: int, per_client: int) -> list[tuple[str, Path]]:
    """Take up to `per_client` EP folders from each of up to `max_clients`
    top-level client directories, searching each client subtree up to 2
    levels deep. Stratified this way so one large/legacy client subtree
    (observed to dominate a plain walk) doesn't swamp the sample."""
    samples: list[tuple[str, Path]] = []
    try:
        client_dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError as e:
        print(f"Cannot list root: {e}")
        return samples

    clients_used = 0
    for client_dir in client_dirs:
        if clients_used >= max_clients:
            break

        found_here: list[tuple[str, Path]] = []
        try:
            for dirpath, dirnames, _filenames in os.walk(client_dir, onerror=lambda e: None):
                depth = len(Path(dirpath).relative_to(client_dir).parts)
                if depth >= 2:
                    dirnames[:] = []
                    continue
                for name in dirnames:
                    m = EP_NAME_RE.match(name)
                    if m and not UTILITY_SUFFIX_RE.search(name):
                        found_here.append((m.group(1), Path(dirpath) / name))
                dirnames[:] = [d for d in dirnames if not ANY_EP_FOLDER_RE.match(d)]
                if len(found_here) >= per_client:
                    break
        except OSError:
            continue

        if found_here:
            samples.extend(found_here[:per_client])
            clients_used += 1

    return samples


def main() -> None:
    root = Path(sys.argv[1])
    max_clients = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    per_client = int(sys.argv[3]) if len(sys.argv) > 3 else 2

    print(f"Sampling up to {per_client} EP folder(s) from up to {max_clients} clients under {root}...")
    samples = sample_ep_folders(root, max_clients, per_client)
    print(f"Sampled {len(samples)} (ep_number, folder) pairs\n")

    # --- Metric 1: retrieval (root search by number alone) ---
    retrieval_found = retrieval_ambiguous = retrieval_correct = 0
    retrieval_issues: list[str] = []

    for ep_number, expected_folder in samples:
        matches = find_ep_folders(root, ep_number)
        if not matches:
            retrieval_issues.append(f"EP-{ep_number}: root search found NOTHING (expected {expected_folder})")
            continue
        retrieval_found += 1
        if len(matches) > 1:
            retrieval_ambiguous += 1
        if expected_folder in matches:
            retrieval_correct += 1
        else:
            retrieval_issues.append(
                f"EP-{ep_number}: expected folder not among {len(matches)} match(es): {expected_folder}"
            )

    # --- Metric 2: classification (given the correct folder directly) ---
    drf_found = ds_found = both_found = 0
    classification_issues: list[str] = []

    for ep_number, folder in samples:
        drf = find_drf_candidates(folder)
        ds = find_design_sheet_candidates(folder)
        if drf:
            drf_found += 1
        else:
            classification_issues.append(f"EP-{ep_number}: no DRF in {folder}")
        if ds:
            ds_found += 1
        else:
            classification_issues.append(f"EP-{ep_number}: no Design Sheet in {folder}")
        if drf and ds:
            both_found += 1

    total = len(samples)
    print("=== Retrieval (root search by EP number) ===")
    print(f"Folder found:              {retrieval_found}/{total}")
    print(f"Correct folder in results: {retrieval_correct}/{total}")
    print(f"Ambiguous (>1 match):      {retrieval_ambiguous}/{total}")

    print("\n=== Classification (given the correct folder) ===")
    print(f"DRF found:           {drf_found}/{total}")
    print(f"Design Sheet found:  {ds_found}/{total}")
    print(f"Both found:          {both_found}/{total}")

    print("\n=== Retrieval issues ===")
    for issue in retrieval_issues:
        print(f" - {issue}")

    print("\n=== Classification issues ===")
    for issue in classification_issues:
        print(f" - {issue}")


if __name__ == "__main__":
    main()
