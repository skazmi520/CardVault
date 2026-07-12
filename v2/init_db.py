"""
CardVault v2 — create a fresh, empty v2 database (no v1 install required).

Usage:  python3 -m v2.init_db

For migrating an existing v1 database instead, use:
        python3 -m v2.migrate_v1_to_v2
"""

import sys

from . import db as v2db


def main():
    if v2db.V2_DB_PATH.exists():
        print(f"v2 database already exists at {v2db.V2_DB_PATH} — nothing to do.")
        return
    if v2db.V1_DB_PATH.exists():
        print(f"NOTE: a v1 database exists at {v2db.V1_DB_PATH}.")
        print("      To carry your v1 data into v2, run instead:")
        print("      python3 -m v2.migrate_v1_to_v2")
        if input("Create an EMPTY v2 database anyway? [y/N] ").strip().lower() != "y":
            sys.exit(1)
    path = v2db.init_fresh()
    conn = v2db.get_connection()   # proves the guard accepts it
    n = len(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    conn.close()
    print(f"Created fresh v2 database at {path} ({n} tables).")
    print("Start the app with:  python3 -m v2.app")


if __name__ == "__main__":
    main()
