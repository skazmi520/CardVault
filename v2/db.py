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

SCHEMA_VERSION = 7

GRADING_COMPANIES = ["PSA", "BGS", "CGC", "TAG"]
PAYMENT_METHODS   = ["cash", "venmo", "zelle", "paypal", "trade", "mixed"]

# Card lifecycle status values (v2)
#   graded_cards:   active | disposed | cracked | removed | lost
#   ungraded_cards: active | submitted_for_grading | promoted | disposed | removed | lost
# 'cracked' = the slab was broken out and the card returned to raw. The row is
# kept as history (what it graded, its cert) but is NOT a disposal — nothing was
# sold and no proceeds were realized; the basis moves to the new raw row.
#
# 'removed' vs 'lost' both take a card out of inventory without a sale, but they
# are opposite economically and must not be merged:
#   removed = a deliberate reclassification. Pulled into the binder, cracked for
#             personal use, or entered by mistake. You still have the value, so
#             there is no gain and no loss — realized_gain stays NULL.
#   lost    = the card is gone. Misplaced, stolen, or moved in a deal that was
#             never written down. The basis is money that will not come back, so
#             it books as a realized loss of the full basis.
CARD_STATUSES = ["active", "disposed", "cracked", "submitted_for_grading",
                 "promoted", "removed", "lost"]

# reasons a card can be marked lost — kept small so the data stays groupable
LOST_REASONS = ["misplaced", "stolen", "moved without a record", "damaged beyond value"]


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
    # a stuck writer (e.g. a leaked connection mid-transaction) should make
    # others wait rather than instantly fail with "database is locked"
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def record_event(conn: sqlite3.Connection, table: str, card_id: int, field: str,
                 old, new, source: str, ref: str = ""):
    """Append one change to card_events. Never commits; rides the caller's
    transaction so the change and its record land together or not at all."""
    conn.execute(
        "INSERT INTO card_events (card_table, card_id, at, field, old_value, new_value, "
        "source, ref) VALUES (?,?,?,?,?,?,?,?)",
        (table, card_id, datetime.now().isoformat(timespec="seconds"), field,
         None if old is None else str(old), None if new is None else str(new),
         source, ref))


def update_card(conn: sqlite3.Connection, table: str, card_id: int, fields: dict,
                source: str, ref: str = "") -> dict:
    """The one way to change fields on an existing card: writes them and logs
    every value that actually changed to card_events. Never commits. Returns
    {field: (old, new)} for what changed. Raises ValueError if the card does
    not exist or a field is generated (those can never be written)."""
    if table not in ("graded_cards", "ungraded_cards"):
        raise ValueError(f"unknown table {table!r}")
    generated = {"acquisition_price", "purchase_price", "basis_unknown", "realized_gain"}
    bad = generated & set(fields)
    if bad:
        raise ValueError(f"{sorted(bad)} are computed and cannot be written")
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (card_id,)).fetchone()
    if row is None:
        raise ValueError(f"card id={card_id} not found in {table}")
    changed = {k: (row[k], v) for k, v in fields.items() if row[k] != v}
    if changed:
        sets = ", ".join(f"{k}=?" for k in changed)
        conn.execute(f"UPDATE {table} SET {sets} WHERE id=?",
                     [v for _, v in changed.values()] + [card_id])
        for k, (old, new) in changed.items():
            record_event(conn, table, card_id, k, old, new, source, ref)
    return changed


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
            # A zero-byte file is not a backup. sqlite3.connect() used to create
            # the file before the copy ran, and when the copy then failed the
            # exception was swallowed — leaving an empty file that this very
            # check treated as "already done" every day after. The 2026-08-10
            # snapshot was such a file: no restore point for that day at all.
            if dest_path.exists() and dest_path.stat().st_size > 0:
                continue                      # already backed up today
            tmp = dest_path.with_suffix(".db.part")
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                tmp.unlink(missing_ok=True)
                dst = sqlite3.connect(tmp)
                src.backup(dst)               # WAL-safe consistent snapshot
                n = dst.execute("SELECT COUNT(*) FROM graded_cards").fetchone()[0]
                dst.close()
                if n <= 0:
                    raise RuntimeError("snapshot has no cards")
                tmp.replace(dest_path)        # only a verified copy gets the real name
                # prune old snapshots — only our own v2 dailies
                old = sorted(dest_dir.glob("cardvault_v2_????-??-??.db"))
                for f in old[:-keep]:
                    f.unlink(missing_ok=True)
            except Exception:
                tmp.unlink(missing_ok=True)   # never leave a partial file behind
                dest_path.unlink(missing_ok=True) if dest_path.exists() and dest_path.stat().st_size == 0 else None
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

-- exactly what was typed on the deal screen, verbatim, at save time. The
-- engine's booked numbers live on the cards; this is the evidence of what
-- they were derived from, so "did I type $400 or did the app invent $700?"
-- is never again unanswerable.
CREATE TABLE IF NOT EXISTS deal_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id      INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    entered_at   TEXT NOT NULL,
    payload_json TEXT NOT NULL
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

-- One row per card line of a deal: what the user typed next to what the engine
-- derived, never in the same column. basis_at_deal / market_at_deal freeze
-- the outgoing card as it stood when the deal was booked, so the deal can be
-- replayed later with the same inputs (tools/replay_deal.py).
CREATE TABLE IF NOT EXISTS deal_lines (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id        INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    side           TEXT    NOT NULL,          -- 'out' | 'in'
    card_table     TEXT    NOT NULL,
    card_id        INTEGER NOT NULL,
    entered_value  REAL,                      -- typed per-card price (out: split pin, in: take-in)
    entered_market REAL,                      -- typed market (in lines)
    derived_value  REAL    NOT NULL,          -- out: proceeds booked; in: basis booked
    basis_at_deal  REAL,                      -- out: the card's basis when it left
    market_at_deal REAL,                      -- out: the card's stored market when it left
    market_set_at  TEXT,                      -- out: when that market had been set
    weight_used    REAL                       -- the split weight the engine used
);
CREATE INDEX IF NOT EXISTS idx_deal_lines_deal ON deal_lines(deal_id);

-- Every change to a card field, with who made it. source: you | engine |
-- reprice | import | correction. A value is never changed without a row here.
CREATE TABLE IF NOT EXISTS card_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    card_table TEXT    NOT NULL,
    card_id    INTEGER NOT NULL,
    at         TEXT    NOT NULL,
    field      TEXT    NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    source     TEXT    NOT NULL,
    ref        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_card_events_card ON card_events(card_table, card_id);

-- A void deletes the deal and the cards it created, and Edit deal replaces a
-- deal with a new revision; either way the full prior record is archived
-- here first, so nothing is ever unrecoverable.
CREATE TABLE IF NOT EXISTS voided_deals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id       INTEGER NOT NULL,
    voided_at     TEXT    NOT NULL,
    reason        TEXT    NOT NULL DEFAULT 'void',   -- 'void' | 'edited'
    snapshot_json TEXT    NOT NULL
);
"""

# Schema 7 (2026-09-25): typed and derived values live in separate columns.
# The basis readers use (acquisition_price / purchase_price), basis_unknown and
# realized_gain are GENERATED: no code can write them, so a derived number can
# never overwrite a typed one and a gain can never go stale.
#   basis_entered  what the user typed (create, drawer edit, typed take-in)
#   basis_derived  what the engine computed (blank take-in split, promote, crack)
#   basis_legacy   pre-schema-7 values whose provenance is unknown
_BASIS = "COALESCE(basis_entered, basis_derived, basis_legacy)"

_GRADED_V7 = f"""
CREATE TABLE graded_cards_v7 (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_number        TEXT    NOT NULL DEFAULT '',
    grading_company      TEXT    NOT NULL,
    grade                TEXT    NOT NULL DEFAULT '',
    card_name            TEXT    NOT NULL,
    card_number          TEXT    NOT NULL DEFAULT '',
    set_name             TEXT    NOT NULL DEFAULT '',
    year                 TEXT    NOT NULL DEFAULT '',
    photo_filename       TEXT,
    acquisition_type     TEXT    NOT NULL DEFAULT 'Cash',
    basis_entered        REAL,
    basis_derived        REAL,
    basis_legacy         REAL,
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
    sale_date            TEXT,
    acquired_via_deal_id INTEGER REFERENCES deals(id),
    disposed_via_deal_id INTEGER REFERENCES deals(id),
    disposed_at          TEXT,
    disposal_proceeds    REAL,
    status               TEXT    NOT NULL DEFAULT 'active',
    expected_grade       TEXT,
    is_pc                INTEGER NOT NULL DEFAULT 0,
    sub_type             TEXT,
    acquisition_price    REAL    GENERATED ALWAYS AS ({_BASIS}) VIRTUAL,
    basis_unknown        INTEGER GENERATED ALWAYS AS ({_BASIS} IS NULL) VIRTUAL,
    realized_gain        REAL    GENERATED ALWAYS AS (
        CASE WHEN status IN ('disposed', 'lost') AND {_BASIS} IS NOT NULL
                  AND COALESCE(disposal_proceeds, sale_price) IS NOT NULL
             THEN ROUND(COALESCE(disposal_proceeds, sale_price) - {_BASIS}, 2) END) VIRTUAL
)"""

_UNGRADED_V7 = f"""
CREATE TABLE ungraded_cards_v7 (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    card_name              TEXT    NOT NULL,
    card_number            TEXT    NOT NULL DEFAULT '',
    set_name               TEXT    NOT NULL DEFAULT '',
    year                   TEXT    NOT NULL DEFAULT '',
    photo_filename         TEXT,
    basis_entered          REAL,
    basis_derived          REAL,
    basis_legacy           REAL,
    purchase_date          TEXT    NOT NULL,
    acquisition_type       TEXT    NOT NULL DEFAULT 'Cash',
    trade_value            REAL    NOT NULL DEFAULT 0,
    trade_details          TEXT    NOT NULL DEFAULT '',
    notes                  TEXT    NOT NULL DEFAULT '',
    grading_status         TEXT    NOT NULL DEFAULT 'Not Slated',
    target_grading_company TEXT    NOT NULL DEFAULT '',
    is_favorited           INTEGER NOT NULL DEFAULT 0,
    date_added             TEXT    NOT NULL,
    is_converted           INTEGER NOT NULL DEFAULT 0,
    market_value           REAL,
    market_value_updated   TEXT,
    acquired_via_deal_id   INTEGER REFERENCES deals(id),
    disposed_via_deal_id   INTEGER REFERENCES deals(id),
    disposed_at            TEXT,
    disposal_proceeds      REAL,
    status                 TEXT    NOT NULL DEFAULT 'active',
    submitted_at           TEXT,
    expected_grade         TEXT,
    is_pc                  INTEGER NOT NULL DEFAULT 0,
    sub_type               TEXT,
    expected_back          TEXT,
    psa_cert               TEXT,
    psa_order              TEXT,
    purchase_price         REAL    GENERATED ALWAYS AS ({_BASIS}) VIRTUAL,
    basis_unknown          INTEGER GENERATED ALWAYS AS ({_BASIS} IS NULL) VIRTUAL,
    realized_gain          REAL    GENERATED ALWAYS AS (
        CASE WHEN status IN ('disposed', 'lost') AND {_BASIS} IS NOT NULL
                  AND disposal_proceeds IS NOT NULL
             THEN ROUND(disposal_proceeds - {_BASIS}, 2) END) VIRTUAL
)"""

# the three columns schema 7 makes generated, per table
_OLD_BASIS_COL = {"graded_cards": "acquisition_price", "ungraded_cards": "purchase_price"}


def _rebuild_card_tables_v7(conn: sqlite3.Connection):
    """One-time rebuild of both card tables into the schema-7 shape.

    Every existing basis moves to basis_legacy (its provenance is unknown);
    a card flagged basis_unknown gets no basis at all, so it stays unknown.
    Stored realized_gain is dropped and recomputed by the generated column.
    Ids and AUTOINCREMENT counters are preserved. Runs in one transaction."""
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        seqs = dict(conn.execute("SELECT name, seq FROM sqlite_sequence").fetchall())
        for table, ddl in (("graded_cards", _GRADED_V7), ("ungraded_cards", _UNGRADED_V7)):
            conn.execute(ddl)
            old_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            new_cols = [r[1] for r in conn.execute(f"PRAGMA table_xinfo({table}_v7)")
                        if r[6] == 0]                  # hidden=0: not generated
            carried = [c for c in new_cols if c in old_cols]
            basis = _OLD_BASIS_COL[table]
            cols = carried + ["basis_legacy"]
            conn.execute(
                f"INSERT INTO {table}_v7 ({', '.join(cols)}) "
                f"SELECT {', '.join(carried)}, "
                f"CASE WHEN basis_unknown=1 THEN NULL ELSE {basis} END FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_v7 RENAME TO {table}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN (?, ?)",
                         (table, f"{table}_v7"))
            if table in seqs:
                conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)",
                             (table, seqs[table]))
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"foreign key check failed after rebuild: {bad[:5]}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

# New columns for both card tables. Existing columns are preserved exactly.
_CARD_NEW_COLS = [
    ("acquired_via_deal_id", "INTEGER REFERENCES deals(id)"),
    ("disposed_via_deal_id", "INTEGER REFERENCES deals(id)"),
    ("disposed_at",          "TEXT"),
    ("disposal_proceeds",    "REAL"),
    ("realized_gain",        "REAL"),
    ("status",               "TEXT NOT NULL DEFAULT 'active'"),
    # Legacy imports arrived with no purchase price. $0 is not the same claim as
    # "we never knew" — a $0 basis makes a sale look like 100% profit and
    # silently inflates realized gains. Flagged cards are reported as
    # "basis unknown" and left out of gain statistics rather than faked.
    ("basis_unknown",        "INTEGER NOT NULL DEFAULT 0"),
    # The user's grade prediction: set on the raw card, carried to the graded
    # card at promotion so predicted-vs-actual accuracy can be analyzed.
    ("expected_grade",       "TEXT"),
    # Personal-collection pieces: tracked like inventory (net worth, stock
    # checks) but never surfaced as sell candidates or business inventory.
    ("is_pc",                "INTEGER NOT NULL DEFAULT 0"),
    # Grade-prediction bucket, declared BEFORE submission and locked once the
    # card is at grading: 'banker' (measured bets) vs 'casino' (fun gambles).
    ("sub_type",             "TEXT"),
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


def scratch_database(prefix: str = "cv_test_") -> sqlite3.Connection:
    """A throwaway, empty database with the current schema, for tests and
    fuzzers. Built from this file's DDL, never copied from the live database,
    so checks exercise the schema the code defines."""
    import tempfile
    path = Path(tempfile.mkdtemp(prefix=prefix)) / "t.db"
    init_fresh(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate_schema(conn: sqlite3.Connection):
    """Apply v2 schema additions. Idempotent."""
    conn.executescript(_V2_DDL)

    for table in ("graded_cards", "ungraded_cards"):
        # table_xinfo, not table_info: schema 7's generated columns are hidden
        # from table_info and would otherwise be re-added as plain columns
        existing = {r[1] for r in conn.execute(f"PRAGMA table_xinfo({table})")}
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
        # when PSA says the submission should land back home
        if table == "ungraded_cards" and "expected_back" not in existing:
            conn.execute("ALTER TABLE ungraded_cards ADD COLUMN expected_back TEXT")
        # cert + order recorded at submission time, from the PSA order CSV, so
        # a return can be matched by cert instead of guessing from names
        if table == "ungraded_cards" and "psa_cert" not in existing:
            conn.execute("ALTER TABLE ungraded_cards ADD COLUMN psa_cert TEXT")
        if table == "ungraded_cards" and "psa_order" not in existing:
            conn.execute("ALTER TABLE ungraded_cards ADD COLUMN psa_order TEXT")

    # schema 7: typed and derived basis in separate columns (one-time rebuild)
    if "basis_entered" not in {r[1] for r in conn.execute("PRAGMA table_xinfo(graded_cards)")}:
        _rebuild_card_tables_v7(conn)
    # price_history rows say where the number came from: 'entered' (typed or a
    # reprice), 'copied' (the old engine's take-in-as-market), 'legacy'
    if "source" not in {r[1] for r in conn.execute("PRAGMA table_info(price_history)")}:
        conn.execute("ALTER TABLE price_history ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'")
    deal_cols = {r[1] for r in conn.execute("PRAGMA table_info(deals)")}
    if "ask_entered" not in deal_cols:
        conn.execute("ALTER TABLE deals ADD COLUMN ask_entered REAL")
    if "engine_version" not in deal_cols:
        conn.execute("ALTER TABLE deals ADD COLUMN engine_version TEXT")

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
                            disposal_proceeds=sale_price
              is_sold=0  -> status='active'
    ungraded: is_converted=1            -> status='promoted'
              grading_status='At Grading' -> status='submitted_for_grading'
              otherwise                  -> status='active'
    """
    conn.execute("""
        UPDATE graded_cards SET
            status = CASE WHEN is_sold=1 THEN 'disposed' ELSE 'active' END,
            disposed_at = CASE WHEN is_sold=1 THEN sale_date ELSE NULL END,
            disposal_proceeds = CASE WHEN is_sold=1 THEN sale_price ELSE NULL END
    """)                                # realized_gain is generated (schema 7)
    conn.execute("""
        UPDATE ungraded_cards SET
            status = CASE
                WHEN is_converted=1 THEN 'promoted'
                WHEN grading_status='At Grading' THEN 'submitted_for_grading'
                ELSE 'active' END
    """)
    conn.commit()
