r"""Point this PC at a shared data folder, so its projects follow the user.

    python -m scripts.use_shared_data "C:\Users\me\Juma Al Majid\EP Platform"
    python -m scripts.use_shared_data "..." --apply

Without --apply it only says what it would do. With it:

  * the folder is made if it is not there;
  * a database already in the folder is kept and adopted -- the richer one
    of two PCs is therefore the one to set up first;
  * a folder with no database is given this PC's, so nothing here is lost;
  * DATA_ROOT is written into backend/.env (the old .env is kept as
    .env.bak), which also moves the uploads, the caches and the backups
    into the folder.

Run it on every PC with the same folder, restart the server, and each PC
opens the same projects. Open the platform on one PC at a time: the
database is one file and OneDrive syncs it as one.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LOCAL_DB = BACKEND / "ep_platform.db"
ENV = BACKEND / ".env"


def projects_in(database: Path) -> int | None:
    """How many projects a database holds; None when it is not one."""
    if not database.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    except sqlite3.Error:
        return None


def env_lines() -> list[str]:
    return ENV.read_text(encoding="utf-8").splitlines() if ENV.is_file() else []


def set_data_root(folder: Path) -> str:
    """Write DATA_ROOT into backend/.env, keeping the old file beside it."""
    lines = env_lines()
    written = False
    out: list[str] = []
    for line in lines:
        if line.strip().startswith("DATA_ROOT="):
            out.append(f"DATA_ROOT={folder}")
            written = True
        else:
            out.append(line)
    if not written:
        out += ["", "# The shared data folder: the database, the uploads, the caches and the", "# backups live here, so every PC pointed at it opens the same projects.", f"DATA_ROOT={folder}"]
    if ENV.is_file():
        shutil.copy2(ENV, ENV.with_suffix(".bak"))
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    return f"DATA_ROOT={folder} written to {ENV} (the old file is {ENV.with_suffix('.bak')})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", help="the shared folder, the same one on every PC")
    parser.add_argument("--apply", action="store_true", help="make the change (without this it only reports)")
    args = parser.parse_args(argv)

    folder = Path(args.folder).expanduser()
    shared_db = folder / "ep_platform.db"
    here, there = projects_in(LOCAL_DB), projects_in(shared_db)

    print(f"This PC's database : {LOCAL_DB} ({here} projects)" if here is not None else f"This PC's database : {LOCAL_DB} (none yet)")
    print(f"The shared folder  : {folder} ({there} projects)" if there is not None else f"The shared folder  : {folder} (no database yet)")

    if there is None:
        plan = "copy this PC's database into the folder, then use it from there"
    elif here is None:
        plan = "use the database already in the folder"
    else:
        plan = (f"use the database already in the folder ({there} projects); this PC's copy ({here} projects) is left "
                f"where it is, so nothing is lost -- run scripts/compare_databases.py to see what only it holds")
    print(f"Plan               : {plan}")

    if not args.apply:
        print("\nNothing was changed. Run it again with --apply to make it so.")
        return 0

    folder.mkdir(parents=True, exist_ok=True)
    if there is None and LOCAL_DB.is_file():
        shutil.copy2(LOCAL_DB, shared_db)
        print(f"Copied {LOCAL_DB} -> {shared_db}")
        kept = LOCAL_DB.with_name(f"ep_platform-before-sharing-{datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.copy2(LOCAL_DB, kept)
        print(f"Kept a copy of this PC's database as {kept}")
    print(set_data_root(folder))
    print("\nRestart the server. Every PC with this DATA_ROOT opens the same projects; open one PC at a time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
