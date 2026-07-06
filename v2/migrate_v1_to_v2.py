"""
CardVault v2 — one-time v1 → v2 duplication + schema migration.

Usage:
    python3 -m v2.migrate_v1_to_v2            # refuses if v2 db already exists
    python3 -m v2.migrate_v1_to_v2 --force    # replace existing v2 db

Steps:
  1. Open v1 READ-ONLY (sqlite URI mode=ro — writes are impossible).
  2. Copy it to cardvault_v2.db via the SQLite backup API (WAL-safe snapshot).
  3. Apply v2 schema additions (deals, deal_photos, new card columns, marker).
  4. Backfill status / disposal fields from v1 state.
  5. Run v1-vs-v2 integrity checks and print the report.

v1 is never written. The only file created/modified is cardvault_v2.db.
"""

import sqlite3
import sys

from . import db as v2db


def duplicate_v1(force: bool = False):
    if not v2db.V1_DB_PATH.exists():
        sys.exit(f"v1 database not found at {v2db.V1_DB_PATH}")
    if v2db.V2_DB_PATH.exists():
        if not force:
            sys.exit(f"{v2db.V2_DB_PATH} already exists. Re-run with --force to replace it.")
        v2db.V2_DB_PATH.unlink()
        for suffix in ("-wal", "-shm"):
            p = v2db.V2_DB_PATH.with_name(v2db.V2_DB_PATH.name + suffix)
            p.unlink(missing_ok=True)

    src = v2db.open_v1_readonly()
    dst = sqlite3.connect(v2db.V2_DB_PATH)
    try:
        src.backup(dst)   # consistent snapshot, safe with WAL
    finally:
        dst.close()
        src.close()
    print(f"Copied v1 -> {v2db.V2_DB_PATH}")


def integrity_report() -> bool:
    """Compare v1 (read-only) with v2. Returns True if all checks pass."""
    v1 = v2db.open_v1_readonly()
    v2 = v2db.get_connection()

    def one(conn, sql):
        return conn.execute(sql).fetchone()[0]

    checks = [
        ("graded rows",            "SELECT COUNT(*) FROM graded_cards"),
        ("graded active",          "SELECT COUNT(*) FROM graded_cards WHERE is_sold=0"),
        ("graded sold",            "SELECT COUNT(*) FROM graded_cards WHERE is_sold=1"),
        ("sum acquisition_price",  "SELECT ROUND(COALESCE(SUM(acquisition_price),0),2) FROM graded_cards"),
        ("sum market_value",       "SELECT ROUND(COALESCE(SUM(market_value),0),2) FROM graded_cards"),
        ("sum sale_price",         "SELECT ROUND(COALESCE(SUM(sale_price),0),2) FROM graded_cards WHERE is_sold=1"),
        ("raw rows",               "SELECT COUNT(*) FROM ungraded_cards"),
        ("raw active",             "SELECT COUNT(*) FROM ungraded_cards WHERE is_converted=0"),
        ("sum raw purchase_price", "SELECT ROUND(COALESCE(SUM(purchase_price),0),2) FROM ungraded_cards"),
        ("portfolio snapshots",    "SELECT COUNT(*) FROM portfolio_snapshots"),
    ]

    print(f"\n{'check':<26}{'v1':>14}{'v2':>14}   result")
    print("-" * 62)
    ok = True
    for label, sql in checks:
        a, b = one(v1, sql), one(v2, sql)
        match = (a == b)
        ok &= match
        print(f"{label:<26}{a:>14}{b:>14}   {'OK' if match else 'MISMATCH'}")

    # v2-only backfill sanity
    disposed  = one(v2, "SELECT COUNT(*) FROM graded_cards WHERE status='disposed'")
    active    = one(v2, "SELECT COUNT(*) FROM graded_cards WHERE status='active'")
    promoted  = one(v2, "SELECT COUNT(*) FROM ungraded_cards WHERE status='promoted'")
    submitted = one(v2, "SELECT COUNT(*) FROM ungraded_cards WHERE status='submitted_for_grading'")
    gain      = one(v2, "SELECT ROUND(COALESCE(SUM(realized_gain),0),2) FROM graded_cards")
    print("-" * 62)
    print(f"v2 status backfill: graded active={active} disposed={disposed} | "
          f"raw promoted={promoted} submitted={submitted}")
    print(f"v2 total realized_gain (from backfill): ${gain:,.2f}")

    v1.close()
    v2.close()
    return ok


def main():
    force = "--force" in sys.argv
    duplicate_v1(force=force)

    # migrate the copy — note: connection via a direct open here because the
    # marker doesn't exist yet; the guard applies to all post-migration access.
    conn = sqlite3.connect(v2db.V2_DB_PATH)
    conn.row_factory = sqlite3.Row
    v2db.migrate_schema(conn)
    v2db.backfill_status(conn)
    conn.close()
    print("v2 schema applied (deals, deal_photos, card columns, marker) and status backfilled.")

    ok = integrity_report()
    print("\nALL INTEGRITY CHECKS PASSED" if ok else "\nINTEGRITY CHECK FAILURES — see above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
