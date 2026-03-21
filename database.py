"""
CardVault Mac — SQLite database layer.
Data lives in ~/.cardvaultmac/cardvault.db
Photos live in ~/.cardvaultmac/photos/
"""

import sqlite3
import os
import shutil
from datetime import datetime, date
from pathlib import Path

DATA_DIR  = Path.home() / ".cardvaultmac"
PHOTO_DIR = DATA_DIR / "photos"
DB_PATH   = DATA_DIR / "cardvault.db"

# ── grading companies / statuses ──────────────────────────────────────────────
GRADING_COMPANIES = ["PSA", "BGS", "CGC", "TAG"]
GRADING_STATUSES  = ["Not Slated", "Slated", "At Grading"]
ACQUISITION_TYPES = ["Cash", "Trade"]

COMPANY_COLORS = {
    "PSA": "#FF3B30",
    "BGS": "#007AFF",
    "CGC": "#AF52DE",
    "TAG": "#5AC8FA",
}

def _ensure_dirs():
    DATA_DIR.mkdir(exist_ok=True)
    PHOTO_DIR.mkdir(exist_ok=True)

def get_connection() -> sqlite3.Connection:
    _ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    _ensure_dirs()
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript("""
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
            notes                  TEXT    NOT NULL DEFAULT '',
            grading_status         TEXT    NOT NULL DEFAULT 'Not Slated',
            target_grading_company TEXT    NOT NULL DEFAULT '',
            is_favorited           INTEGER NOT NULL DEFAULT 0,
            date_added             TEXT    NOT NULL,
            is_converted           INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.commit()

    # ── migrations: add columns that may not exist in older DBs ──────────────
    _migrate(conn)
    conn.close()

def _migrate(conn: sqlite3.Connection):
    """Safely add new columns to existing databases."""
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(graded_cards)")
    }
    if "grading_fee" not in existing:
        conn.execute("ALTER TABLE graded_cards ADD COLUMN grading_fee REAL NOT NULL DEFAULT 0")
        conn.commit()

def _today() -> str:
    return date.today().isoformat()

def _now() -> str:
    return datetime.now().isoformat()

# ── photo helpers ──────────────────────────────────────────────────────────────

def save_photo(src_path: str) -> str:
    """Copy an image into the photos dir, return filename."""
    _ensure_dirs()
    ext = Path(src_path).suffix.lower() or ".jpg"
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}"
    dst = PHOTO_DIR / filename
    shutil.copy2(src_path, dst)
    return filename

def photo_path(filename: str | None) -> str | None:
    if not filename:
        return None
    p = PHOTO_DIR / filename
    return str(p) if p.exists() else None

def delete_photo(filename: str | None):
    if filename:
        p = PHOTO_DIR / filename
        if p.exists():
            p.unlink(missing_ok=True)

# ── GRADED CARDS ───────────────────────────────────────────────────────────────

def add_graded_card(
    serial_number: str,
    grading_company: str,
    grade: str,
    card_name: str,
    card_number: str,
    set_name: str,
    photo_filename: str | None,
    acquisition_type: str,
    acquisition_price: float,
    acquisition_date: str,
    notes: str,
    grading_fee: float = 0.0,
) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO graded_cards
           (serial_number, grading_company, grade, card_name, card_number,
            set_name, photo_filename, acquisition_type, acquisition_price,
            grading_fee, acquisition_date, notes, date_added)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (serial_number, grading_company, grade, card_name, card_number,
         set_name, photo_filename, acquisition_type, acquisition_price,
         grading_fee, acquisition_date, notes, _now()),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id

def get_graded_cards(sold: bool = False) -> list[sqlite3.Row]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM graded_cards WHERE is_sold=? ORDER BY acquisition_date DESC",
        (1 if sold else 0,)
    ).fetchall()
    conn.close()
    return rows

def get_graded_card(card_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM graded_cards WHERE id=?", (card_id,)).fetchone()
    conn.close()
    return row

def update_graded_card(card_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [card_id]
    conn = get_connection()
    conn.execute(f"UPDATE graded_cards SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()

def mark_graded_sold(card_id: int, sale_price: float, sale_date: str):
    update_graded_card(card_id, is_sold=1, sale_price=sale_price, sale_date=sale_date)

def delete_graded_card(card_id: int):
    conn = get_connection()
    row = conn.execute("SELECT photo_filename FROM graded_cards WHERE id=?", (card_id,)).fetchone()
    if row and row["photo_filename"]:
        delete_photo(row["photo_filename"])
    conn.execute("DELETE FROM graded_cards WHERE id=?", (card_id,))
    conn.commit()
    conn.close()

# ── UNGRADED CARDS ─────────────────────────────────────────────────────────────

def add_ungraded_card(
    card_name: str,
    card_number: str,
    set_name: str,
    year: str,
    photo_filename: str | None,
    purchase_price: float,
    purchase_date: str,
    notes: str,
    grading_status: str,
    target_grading_company: str,
) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO ungraded_cards
           (card_name, card_number, set_name, year, photo_filename,
            purchase_price, purchase_date, notes, grading_status,
            target_grading_company, date_added)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (card_name, card_number, set_name, year, photo_filename,
         purchase_price, purchase_date, notes, grading_status,
         target_grading_company, _now()),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id

def get_ungraded_cards(converted: bool = False) -> list[sqlite3.Row]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ungraded_cards WHERE is_converted=? ORDER BY date_added DESC",
        (1 if converted else 0,)
    ).fetchall()
    conn.close()
    return rows

def get_ungraded_card(card_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM ungraded_cards WHERE id=?", (card_id,)).fetchone()
    conn.close()
    return row

def update_ungraded_card(card_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [card_id]
    conn = get_connection()
    conn.execute(f"UPDATE ungraded_cards SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()

def delete_ungraded_card(card_id: int):
    conn = get_connection()
    row = conn.execute("SELECT photo_filename FROM ungraded_cards WHERE id=?", (card_id,)).fetchone()
    if row and row["photo_filename"]:
        delete_photo(row["photo_filename"])
    conn.execute("DELETE FROM ungraded_cards WHERE id=?", (card_id,))
    conn.commit()
    conn.close()

def convert_ungraded_to_graded(
    ungraded_id: int,
    serial_number: str,
    grading_company: str,
    grade: str,
    acquisition_date: str,
) -> int:
    """Mark ungraded as converted and create the graded record."""
    ug = get_ungraded_card(ungraded_id)
    if not ug:
        raise ValueError(f"No ungraded card with id={ungraded_id}")

    # Duplicate photo for the graded card
    new_photo = None
    if ug["photo_filename"]:
        src = photo_path(ug["photo_filename"])
        if src:
            new_photo = save_photo(src)

    graded_id = add_graded_card(
        serial_number=serial_number,
        grading_company=grading_company,
        grade=grade,
        card_name=ug["card_name"],
        card_number=ug["card_number"],
        set_name=ug["set_name"],
        photo_filename=new_photo,
        acquisition_type="Cash",
        acquisition_price=ug["purchase_price"],
        acquisition_date=acquisition_date,
        notes=ug["notes"],
    )
    update_ungraded_card(ungraded_id, is_converted=1)
    return graded_id

# ── ANALYTICS HELPERS ──────────────────────────────────────────────────────────

def get_dashboard_stats() -> dict:
    conn = get_connection()

    inv = conn.execute("SELECT * FROM graded_cards WHERE is_sold=0").fetchall()
    sold = conn.execute("SELECT * FROM graded_cards WHERE is_sold=1").fetchall()

    total_market = sum(
        (r["market_value"] if r["market_value"] is not None else r["acquisition_price"])
        for r in inv
    )
    unrealized = sum(
        (r["market_value"] - r["acquisition_price"])
        for r in inv
        if r["market_value"] is not None
    )
    realized = sum(
        (r["sale_price"] - r["acquisition_price"])
        for r in sold
        if r["sale_price"] is not None
    )
    conn.close()
    return {
        "cards_owned":      len(inv),
        "total_market":     total_market,
        "unrealized_profit": unrealized,
        "realized_profit":  realized,
        "sold_count":       len(sold),
    }

def get_monthly_profits() -> list[dict]:
    """Returns list of {month: 'YYYY-MM', profit: float}."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT strftime('%Y-%m', sale_date) AS month,
                  SUM(sale_price - acquisition_price) AS profit
           FROM graded_cards
           WHERE is_sold=1 AND sale_price IS NOT NULL
           GROUP BY month
           ORDER BY month""",
    ).fetchall()
    conn.close()
    return [{"month": r["month"], "profit": r["profit"]} for r in rows]

def get_analytics_summary() -> dict:
    conn = get_connection()
    sold = conn.execute(
        "SELECT * FROM graded_cards WHERE is_sold=1 AND sale_price IS NOT NULL"
    ).fetchall()
    inv  = conn.execute("SELECT * FROM graded_cards WHERE is_sold=0").fetchall()
    conn.close()

    if not sold:
        return {
            "total_sold": 0, "total_revenue": 0, "total_cost_sold": 0,
            "total_profit": 0, "avg_profit": 0, "win_rate": 0,
            "best": None, "worst": None,
            "inventory_count": len(inv),
            "inventory_cost": sum(r["acquisition_price"] for r in inv),
        }

    profits = [(r, r["sale_price"] - r["acquisition_price"]) for r in sold]
    profits.sort(key=lambda x: x[1], reverse=True)

    total_profit = sum(p for _, p in profits)
    wins = sum(1 for _, p in profits if p >= 0)

    return {
        "total_sold":      len(sold),
        "total_revenue":   sum(r["sale_price"] for r in sold),
        "total_cost_sold": sum(r["acquisition_price"] for r in sold),
        "total_profit":    total_profit,
        "avg_profit":      total_profit / len(sold),
        "win_rate":        wins / len(sold) * 100,
        "best":            profits[0][0]  if profits else None,
        "worst":           profits[-1][0] if profits else None,
        "inventory_count": len(inv),
        "inventory_cost":  sum(r["acquisition_price"] for r in inv),
    }

# ── PORTFOLIO SNAPSHOTS ────────────────────────────────────────────────────────

def record_portfolio_snapshot() -> dict:
    """Compute current portfolio value and save a snapshot. Returns the snapshot dict."""
    conn = get_connection()
    inv = conn.execute("SELECT * FROM graded_cards WHERE is_sold=0").fetchall()

    total_value = sum(
        (r["market_value"] if r["market_value"] is not None else r["acquisition_price"])
        for r in inv
    )
    card_count = len(inv)
    today = _today()

    # If a snapshot already exists for today, update it instead of inserting a duplicate
    existing = conn.execute(
        "SELECT id FROM portfolio_snapshots WHERE snapshot_date=?", (today,)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE portfolio_snapshots SET total_value=?, card_count=? WHERE id=?",
            (total_value, card_count, existing["id"])
        )
    else:
        conn.execute(
            "INSERT INTO portfolio_snapshots (snapshot_date, total_value, card_count) VALUES (?,?,?)",
            (today, total_value, card_count)
        )

    conn.commit()
    conn.close()
    return {"date": today, "total_value": total_value, "card_count": card_count}

def get_portfolio_snapshots() -> list[dict]:
    """Return all snapshots ordered oldest → newest."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM portfolio_snapshots ORDER BY snapshot_date ASC"
    ).fetchall()
    conn.close()
    return [{"date": r["snapshot_date"], "total_value": r["total_value"],
             "card_count": r["card_count"]} for r in rows]

# ── INVENTORY AGING ────────────────────────────────────────────────────────────

def get_aging_cards(limit: int = 8) -> list[dict]:
    """Return unsold cards sorted by acquisition_date ascending (held longest first)."""
    from datetime import date as _date
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM graded_cards WHERE is_sold=0 AND acquisition_date != ''
           ORDER BY acquisition_date ASC LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()

    today = _date.today()
    result = []
    for r in rows:
        try:
            acq = _date.fromisoformat(r["acquisition_date"])
            days = (today - acq).days
        except ValueError:
            days = 0
        result.append({"card": r, "days_held": days})
    return result
