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
SLAB_PHOTO_DIR = DATA_DIR / "slab_photos"     # v2-only: slab label photos

BACKUP_DIR  = DATA_DIR / "backups"
ICLOUD_DIR  = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/CardVault Backups"
LOCAL_KEEP  = 7     # daily local snapshots to retain
ICLOUD_KEEP = 30    # daily iCloud snapshots to retain

SCHEMA_VERSION = 3

GRADING_COMPANIES = ["PSA", "BGS", "CGC", "TAG"]
PAYMENT_METHODS   = ["cash", "venmo", "zelle", "paypal", "trade", "mixed"]

# Card lifecycle status values (v2)
#   graded_cards:   active | disposed | cracked
#   ungraded_cards: active | submitted_for_grading | promoted
# 'cracked' = the slab was broken out and the card returned to raw. The row is
# kept as history (what it graded, its cert) but is NOT a disposal — nothing was
# sold and no proceeds were realized; the basis moves to the new raw row.
CARD_STATUSES = ["active", "disposed", "cracked", "submitted_for_grading", "promoted"]


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


def backup_v2():
    """Daily snapshot of the v2 database (local + iCloud). Runs once per day
    on app start; a failure never blocks startup.

    v1 backed itself up on every launch but only ever covered cardvault.db —
    the v2 database (deals, cash ledger, price history) had no coverage at all.
    Files are named cardvault_v2_YYYY-MM-DD.db so they never collide with the
    v1 backups sitting in the same directories.
    """
    if not V2_DB_PATH.exists():
        return
    today = date.today().isoformat()
    destinations = [
        (BACKUP_DIR / f"cardvault_v2_{today}.db", LOCAL_KEEP, BACKUP_DIR),
        (ICLOUD_DIR / f"cardvault_v2_{today}.db", ICLOUD_KEEP, ICLOUD_DIR),
    ]
    src = sqlite3.connect(V2_DB_PATH)
    try:
        for dest_path, keep, dest_dir in destinations:
            if dest_path.exists():
                continue                      # already backed up today
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                dst = sqlite3.connect(dest_path)
                src.backup(dst)               # WAL-safe consistent snapshot
                dst.close()
                # prune old snapshots — only our own v2 dailies
                old = sorted(dest_dir.glob("cardvault_v2_????-??-??.db"))
                for f in old[:-keep]:
                    f.unlink(missing_ok=True)
            except Exception:
                pass                          # one bad destination can't block the other
    finally:
        src.close()


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

-- Phase 4: slab photo ingestion / extraction pipeline
CREATE TABLE IF NOT EXISTS photo_imports (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path          TEXT NOT NULL,
    uploaded_at        TEXT NOT NULL,
    extracted_json     TEXT,                          -- Haiku label extraction
    extract_error      TEXT,
    extract_cost       REAL,                          -- USD, from token usage
    cert_verified_json TEXT,                          -- normalized PSA cert data
    status             TEXT NOT NULL DEFAULT 'pending',  -- pending|extracted|applied|rejected
    matched_table      TEXT,
    matched_id         INTEGER
);

-- PSA cert responses are immutable: cache forever, never re-query
CREATE TABLE IF NOT EXISTS psa_cert_cache (
    cert_number   TEXT PRIMARY KEY,
    fetched_at    TEXT NOT NULL,
    response_json TEXT NOT NULL
);

-- daily lookup budget for the free PSA tier
CREATE TABLE IF NOT EXISTS psa_budget (
    day  TEXT PRIMARY KEY,
    used INTEGER NOT NULL DEFAULT 0
);

-- cash pool: starting balance + manual add/subtract entries.
-- current balance = SUM(cash_ledger.amount) + SUM(deals.cash_amount)
CREATE TABLE IF NOT EXISTS cash_ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    amount      REAL NOT NULL,          -- signed: + deposit, - withdrawal
    memo        TEXT NOT NULL DEFAULT ''
);

-- per-card market value history (recorded on every reprice)
CREATE TABLE IF NOT EXISTS price_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id      INTEGER NOT NULL REFERENCES graded_cards(id),
    recorded_at  TEXT NOT NULL,
    market_value REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_card ON price_history(card_id, recorded_at);
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


# Base card tables for a FRESH v2 install (no v1 to copy). Column-compatible
# with v1 so migrate_v1_to_v2 and init_fresh produce identical schemas.
_BASE_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT    NOT NULL,
    total_value   REAL    NOT NULL,
    card_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS graded_cards (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_number        TEXT    NOT NULL DEFAULT '',
    grading_company      TEXT    NOT NULL,
    grade                TEXT    NOT NULL DEFAULT '',
    card_name            TEXT    NOT NULL,
    card_number          TEXT    NOT NULL DEFAULT '',
    set_name             TEXT    NOT NULL DEFAULT '',
    photo_filename       TEXT,
    acquisition_type     TEXT    NOT NULL DEFAULT 'Cash',
    acquisition_price    REAL    NOT NULL DEFAULT 0,
    grading_fee          REAL    NOT NULL DEFAULT 0,
    trade_value          REAL    NOT NULL DEFAULT 0,
    trade_details        TEXT    NOT NULL DEFAULT '',
    acquisition_date     TEXT    NOT NULL,
    notes                TEXT    NOT NULL DEFAULT '',
    date_added           TEXT    NOT NULL,
    market_value         REAL,
    market_value_updated TEXT,
    is_favorited         INTEGER NOT NULL DEFAULT 0,
    is_sold              INTEGER NOT NULL DEFAULT 0,
    sale_price           REAL,
    sale_date            TEXT
);

CREATE TABLE IF NOT EXISTS ungraded_cards (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    card_name              TEXT    NOT NULL,
    card_number            TEXT    NOT NULL DEFAULT '',
    set_name               TEXT    NOT NULL DEFAULT '',
    year                   TEXT    NOT NULL DEFAULT '',
    photo_filename         TEXT,
    purchase_price         REAL    NOT NULL DEFAULT 0,
    purchase_date          TEXT    NOT NULL,
    acquisition_type       TEXT    NOT NULL DEFAULT 'Cash',
    trade_value            REAL    NOT NULL DEFAULT 0,
    trade_details          TEXT    NOT NULL DEFAULT '',
    notes                  TEXT    NOT NULL DEFAULT '',
    grading_status         TEXT    NOT NULL DEFAULT 'Not Slated',
    target_grading_company TEXT    NOT NULL DEFAULT '',
    is_favorited           INTEGER NOT NULL DEFAULT 0,
    date_added             TEXT    NOT NULL,
    is_converted           INTEGER NOT NULL DEFAULT 0
);
"""


def init_fresh(db_path: Path | None = None) -> Path:
    """Create a brand-new, EMPTY v2 database (no v1 required).

    Refuses to overwrite an existing database. Returns the path created.
    """
    path = db_path or V2_DB_PATH
    if path.exists():
        raise FileExistsError(f"{path} already exists — refusing to overwrite")
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_BASE_DDL)
        migrate_schema(conn)
    finally:
        conn.close()
    return path


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
        # ungraded_cards gains submitted_at (stamped when sent to grading)
        if table == "ungraded_cards" and "submitted_at" not in existing:
            conn.execute("ALTER TABLE ungraded_cards ADD COLUMN submitted_at TEXT")
        # raw cards can carry a market value too — v1 never tracked one, so a
        # value entered against a raw card in a deal had nowhere to live and
        # was silently dropped.
        if table == "ungraded_cards" and "market_value" not in existing:
            conn.execute("ALTER TABLE ungraded_cards ADD COLUMN market_value REAL")
        if table == "ungraded_cards" and "market_value_updated" not in existing:
            conn.execute("ALTER TABLE ungraded_cards ADD COLUMN market_value_updated TEXT")

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
