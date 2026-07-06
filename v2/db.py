"""
CardVault v2 — database layer.

SAFETY MODEL
============
v1 database:  ~/.cardvaultmac/cardvault.db      — v2 code NEVER writes here.
v2 database:  ~/.cardvaultmac/cardvault_v2.db   — all v2 reads/writes.

Guard: the v2 database carries a `v2_meta` marker table written once during
migration. `get_connection()` refuses to return a connection to any database
that lacks the marker, so v2 code physically cannot run against v1 (or any
other stray SQLite file) even if the path constant is edited by mistake.
"""

import sqlite3
from datetime import datetime, date
from pathlib import Path

DATA_DIR   = Path.home() / ".cardvaultmac"
V1_DB_PATH = DATA_DIR / "cardvault.db"        # never written by v2
V2_DB_PATH = DATA_DIR / "cardvault_v2.db"
PHOTO_DIR  = DATA_DIR / "photos"              # shared, read-only from v2's perspective
DEAL_PHOTO_DIR = DATA_DIR / "deal_photos"     # v2-only

SCHEMA_VERSION = 1

GRADING_COMPANIES = ["PSA", "BGS", "CGC", "TAG"]
PAYMENT_METHODS   = ["cash", "venmo", "zelle", "paypal", "trade", "mixed"]

# Card lifecycle status values (v2)
#   graded_cards:   active | disposed
#   ungraded_cards: active | submitted_for_grading | promoted
CARD_STATUSES = ["active", "disposed", "submitted_for_grading", "promoted"]


class V2GuardError(RuntimeError):
    """Raised when v2 code is pointed at a non-v2 database."""


def _has_v2_marker(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_meta'"
    ).fetchone()
    if not row:
        return False
    return conn.execute("SELECT COUNT(*) FROM v2_meta WHERE key='is_v2'").fetchone()[0] > 0


def get_connection() -> sqlite3.Connection:
    """Open the v2 database. Refuses anything that isn't a marked v2 database."""
    if not V2_DB_PATH.exists():
        raise V2GuardError(
            f"v2 database not found at {V2_DB_PATH}. "
            "Run v2/migrate_v1_to_v2.py first."
        )
    if V2_DB_PATH.resolve() == V1_DB_PATH.resolve():
        raise V2GuardError("v2 path points at the v1 database — refusing to open.")

    conn = sqlite3.connect(V2_DB_PATH)
    conn.row_factory = sqlite3.Row
    if not _has_v2_marker(conn):
        conn.close()
        raise V2GuardError(
            f"{V2_DB_PATH} is not a marked v2 database (v2_meta missing) — refusing to run. "
            "This guard prevents v2 code from ever touching the v1 database."
        )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def open_v1_readonly() -> sqlite3.Connection:
    """Read-only handle to the v1 database (URI mode=ro: writes are impossible)."""
    conn = sqlite3.connect(f"file:{V1_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ── v2 schema migration (applied to the v2 copy only) ─────────────────────────

_V2_DDL = """
CREATE TABLE IF NOT EXISTS v2_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at    TEXT NOT NULL,              -- ISO timestamp
    counterparty   TEXT NOT NULL DEFAULT '',   -- name or table number
    location       TEXT NOT NULL DEFAULT '',
    payment_method TEXT NOT NULL DEFAULT 'cash',
    cash_amount    REAL NOT NULL DEFAULT 0,    -- signed: + cash to me, - cash paid
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deal_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id     INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,
    captured_at TEXT
);
"""

# New columns for both card tables. Existing columns are preserved exactly.
_CARD_NEW_COLS = [
    ("acquired_via_deal_id", "INTEGER REFERENCES deals(id)"),
    ("disposed_via_deal_id", "INTEGER REFERENCES deals(id)"),
    ("disposed_at",          "TEXT"),
    ("disposal_proceeds",    "REAL"),
    ("realized_gain",        "REAL"),
    ("status",               "TEXT NOT NULL DEFAULT 'active'"),
]


def migrate_schema(conn: sqlite3.Connection):
    """Apply v2 schema additions. Idempotent."""
    conn.executescript(_V2_DDL)

    for table in ("graded_cards", "ungraded_cards"):
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in _CARD_NEW_COLS:
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        # graded_cards gains a year column (slab labels carry it; v1 never had one)
        if table == "graded_cards" and "year" not in existing:
            conn.execute("ALTER TABLE graded_cards ADD COLUMN year TEXT NOT NULL DEFAULT ''")

    conn.execute(
        "INSERT OR REPLACE INTO v2_meta (key, value) VALUES ('is_v2', '1')")
    conn.execute(
        "INSERT OR REPLACE INTO v2_meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),))
    conn.execute(
        "INSERT OR IGNORE INTO v2_meta (key, value) VALUES ('migrated_at', ?)",
        (datetime.now().isoformat(),))
    conn.commit()


def backfill_status(conn: sqlite3.Connection):
    """Map v1 state onto the v2 status column and disposal fields. Idempotent.

    graded:   is_sold=1  -> status='disposed', disposed_at=sale_date,
                            disposal_proceeds=sale_price,
                            realized_gain=sale_price-acquisition_price
              is_sold=0  -> status='active'
    ungraded: is_converted=1            -> status='promoted'
              grading_status='At Grading' -> status='submitted_for_grading'
              otherwise                  -> status='active'
    """
    conn.execute("""
        UPDATE graded_cards SET
            status = CASE WHEN is_sold=1 THEN 'disposed' ELSE 'active' END,
            disposed_at = CASE WHEN is_sold=1 THEN sale_date ELSE NULL END,
            disposal_proceeds = CASE WHEN is_sold=1 THEN sale_price ELSE NULL END,
            realized_gain = CASE
                WHEN is_sold=1 AND sale_price IS NOT NULL
                THEN sale_price - acquisition_price ELSE NULL END
    """)
    conn.execute("""
        UPDATE ungraded_cards SET
            status = CASE
                WHEN is_converted=1 THEN 'promoted'
                WHEN grading_status='At Grading' THEN 'submitted_for_grading'
                ELSE 'active' END
    """)
    conn.commit()
