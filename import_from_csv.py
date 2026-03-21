"""
CardVault Mac — Google Sheets CSV importer
==========================================

Usage:
    python3 import_from_csv.py /path/to/csv/folder

Expected files in the folder (case-insensitive, any of these names work):
    PSA.csv, CGC.csv, BGS.csv, TAG.csv   — graded inventory
    SOLD.csv                              — sold cards
    RAW.csv                               — ungraded / raw cards
    Summary.csv                           — ignored automatically

Graded sheet columns  (A–G):
    Card Name, Grade, Purchase Price, Grading Fee,
    Current Value, Potential Profit/Loss, Total Cost

Sold sheet columns  (A–H):
    Card Name, Grade, Purchase Price, Grade Fee,
    Total Cost, Trade Value, Cash Value, Profit/Loss

Raw sheet columns  (A–F):
    Card Name, Purchase Price, Current Value,
    Expected Grade, Graded Price, At PSA?
"""

import csv
import sys
import os
from pathlib import Path
from datetime import date
import database as db

# ── helpers ───────────────────────────────────────────────────────────────────

IMPORT_DATE  = date.today().isoformat()
IMPORT_NOTE  = "Imported from Google Sheets"

def _float(val: str) -> float:
    """Parse a dollar string like '$1,234.56' or '1234.56' or '' → 0.0."""
    if not val or val.strip() in ("", "-", "—", "N/A", "n/a"):
        return 0.0
    cleaned = val.strip().lstrip("$").replace(",", "").replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0

def _str(val: str) -> str:
    return val.strip() if val else ""

def _bool_yes(val: str) -> bool:
    return val.strip().lower() in ("yes", "y", "true", "1", "x")

def _find_csv(folder: Path, *names: str) -> Path | None:
    """Case-insensitive search for any of the given filenames."""
    for name in names:
        for f in folder.iterdir():
            if f.suffix.lower() == ".csv" and f.stem.lower() == name.lower():
                return f
    return None

def _read_csv(path: Path) -> list[dict]:
    """Read a CSV, strip BOM, return list of row dicts with stripped keys."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for row in reader:
            cleaned = {k.strip(): v for k, v in row.items() if k}
            # skip blank rows (all values empty)
            if any(v.strip() for v in cleaned.values()):
                rows.append(cleaned)
        return rows

def _col(row: dict, *candidates: str) -> str:
    """Return first matching column value (case-insensitive), or ''."""
    lower_row = {k.lower(): v for k, v in row.items()}
    for c in candidates:
        v = lower_row.get(c.lower())
        if v is not None:
            return v
    return ""

# ── importers ─────────────────────────────────────────────────────────────────

def import_graded_sheet(path: Path, company: str) -> tuple[int, int]:
    """Import one company sheet (PSA/CGC/BGS/TAG). Returns (imported, skipped)."""
    rows    = _read_csv(path)
    ok = skipped = 0

    for i, row in enumerate(rows, start=2):   # row 2 = first data row
        card_name = _str(_col(row, "Card Name"))
        if not card_name:
            skipped += 1
            continue

        grade         = _str(_col(row, "Grade"))
        purchase_price= _float(_col(row, "Purchase Price"))
        grading_fee   = _float(_col(row, "Grading Fee", "Grading fee"))
        total_cost    = _float(_col(row, "Total Cost", "Total cost"))
        current_value = _float(_col(row, "Current Value", "Current value"))

        # Prefer Total Cost as the cost basis; fall back to purchase + fee
        if total_cost > 0:
            acquisition_price = total_cost
        else:
            acquisition_price = purchase_price + grading_fee

        # If grading fee is zero but we can back-calculate it, do so
        if grading_fee == 0 and total_cost > 0 and purchase_price > 0:
            grading_fee = max(0.0, total_cost - purchase_price)

        market_value = current_value if current_value > 0 else None

        card_id = db.add_graded_card(
            serial_number="",
            grading_company=company,
            grade=grade,
            card_name=card_name,
            card_number="",
            set_name="",
            photo_filename=None,
            acquisition_type="Cash",
            acquisition_price=acquisition_price,
            grading_fee=grading_fee,
            acquisition_date=IMPORT_DATE,
            notes=IMPORT_NOTE,
        )

        if market_value is not None:
            db.update_graded_card(card_id,
                                  market_value=market_value,
                                  market_value_updated=IMPORT_DATE)
        ok += 1

    return ok, skipped


def import_sold_sheet(path: Path) -> tuple[int, int]:
    """Import the SOLD sheet. Returns (imported, skipped)."""
    rows    = _read_csv(path)
    ok = skipped = 0

    for i, row in enumerate(rows, start=2):
        card_name = _str(_col(row, "Card Name"))
        if not card_name:
            skipped += 1
            continue

        grade          = _str(_col(row, "Grade"))
        purchase_price = _float(_col(row, "Purchase Price"))
        grading_fee    = _float(_col(row, "Grade Fee", "Grading Fee", "Grading fee"))
        total_cost     = _float(_col(row, "Total Cost", "Total cost"))
        trade_value    = _float(_col(row, "Trade Value", "Trade value"))
        cash_value     = _float(_col(row, "Cash Value",  "Cash value"))

        # Determine cost basis
        if total_cost > 0:
            acquisition_price = total_cost
        else:
            acquisition_price = purchase_price + grading_fee

        if grading_fee == 0 and total_cost > 0 and purchase_price > 0:
            grading_fee = max(0.0, total_cost - purchase_price)

        # Sale price = cash + trade combined (partial deals are common)
        sale_price = cash_value + trade_value

        # Acquisition type reflects what was received
        if cash_value > 0 and trade_value > 0:
            acquisition_type = "Trade"   # mixed — flag as Trade, breakdown in notes
        elif trade_value > 0:
            acquisition_type = "Trade"
        else:
            acquisition_type = "Cash"

        # Build notes with sale breakdown when both values are present
        notes_parts = [IMPORT_NOTE]
        if cash_value > 0 and trade_value > 0:
            notes_parts.append(
                f"Mixed sale — Cash: ${cash_value:,.2f}  |  Trade: ${trade_value:,.2f}"
            )
        elif trade_value > 0:
            notes_parts.append(f"Trade sale: ${trade_value:,.2f}")
        notes = "  |  ".join(notes_parts)

        # Infer grading company (SOLD sheet has no company column)
        company = _infer_company(row)

        card_id = db.add_graded_card(
            serial_number="",
            grading_company=company,
            grade=grade,
            card_name=card_name,
            card_number="",
            set_name="",
            photo_filename=None,
            acquisition_type=acquisition_type,
            acquisition_price=acquisition_price,
            grading_fee=grading_fee,
            acquisition_date=IMPORT_DATE,
            notes=notes,
        )

        if sale_price > 0:
            db.mark_graded_sold(card_id, sale_price, IMPORT_DATE)

        ok += 1

    return ok, skipped


def import_raw_sheet(path: Path) -> tuple[int, int]:
    """Import the RAW (ungraded) sheet. Returns (imported, skipped)."""
    rows    = _read_csv(path)
    ok = skipped = 0

    for i, row in enumerate(rows, start=2):
        card_name = _str(_col(row, "Card Name"))
        if not card_name:
            skipped += 1
            continue

        purchase_price  = _float(_col(row, "Purchase Price"))
        current_value   = _float(_col(row, "Current Value", "Current value"))
        expected_grade  = _str(_col(row, "Expected Grade", "Expected grade"))
        graded_price    = _float(_col(row, "Graded Price",  "Graded price"))
        at_psa          = _bool_yes(_col(row, "At PSA?", "At PSA", "at psa?"))

        grading_status        = "At Grading" if at_psa else "Not Slated"
        target_company        = "PSA"         if at_psa else ""

        # Build notes with extra context that has no dedicated field
        notes_parts = [IMPORT_NOTE]
        if expected_grade:
            notes_parts.append(f"Expected grade: {expected_grade}")
        if graded_price > 0:
            notes_parts.append(f"Expected graded price: ${graded_price:,.2f}")
        if current_value > 0:
            notes_parts.append(f"Raw current value: ${current_value:,.2f}")
        notes = "  |  ".join(notes_parts)

        db.add_ungraded_card(
            card_name=card_name,
            card_number="",
            set_name="",
            year="",
            photo_filename=None,
            purchase_price=purchase_price,
            purchase_date=IMPORT_DATE,
            notes=notes,
            grading_status=grading_status,
            target_grading_company=target_company,
        )
        ok += 1

    return ok, skipped


def _infer_company(row: dict) -> str:
    """Try to detect grading company from any column in the row."""
    text = " ".join(str(v) for v in row.values()).upper()
    for co in ["PSA", "BGS", "CGC", "TAG"]:
        if co in text:
            return co
    return "PSA"   # default if unknown


# ── main ──────────────────────────────────────────────────────────────────────

def run(folder: str):
    db.init_db()
    folder_path = Path(folder).expanduser().resolve()

    if not folder_path.is_dir():
        print(f"ERROR: '{folder_path}' is not a directory.")
        sys.exit(1)

    print(f"\nCardVault Mac — CSV Import")
    print(f"Folder: {folder_path}")
    print("=" * 50)

    total_ok = total_skipped = 0

    # ── graded company sheets ─────────────────────────────────────────────────
    for company in ["PSA", "CGC", "BGS", "TAG"]:
        csv_path = _find_csv(folder_path, company)
        if csv_path:
            ok, skipped = import_graded_sheet(csv_path, company)
            total_ok      += ok
            total_skipped += skipped
            print(f"  {company}.csv        → {ok:>4} cards imported  ({skipped} skipped)")
        else:
            print(f"  {company}.csv        → not found, skipped")

    # ── sold sheet ────────────────────────────────────────────────────────────
    sold_path = _find_csv(folder_path, "SOLD", "Sold")
    if sold_path:
        ok, skipped = import_sold_sheet(sold_path)
        total_ok      += ok
        total_skipped += skipped
        print(f"  SOLD.csv       → {ok:>4} cards imported  ({skipped} skipped)")
    else:
        print(f"  SOLD.csv       → not found, skipped")

    # ── raw / ungraded sheet ──────────────────────────────────────────────────
    raw_path = _find_csv(folder_path, "RAW", "Raw", "Ungraded")
    if raw_path:
        ok, skipped = import_raw_sheet(raw_path)
        total_ok      += ok
        total_skipped += skipped
        print(f"  RAW.csv        → {ok:>4} cards imported  ({skipped} skipped)")
    else:
        print(f"  RAW.csv        → not found, skipped")

    # ── summary ───────────────────────────────────────────────────────────────
    print("=" * 50)
    print(f"  Total imported : {total_ok}")
    print(f"  Total skipped  : {total_skipped}  (blank rows)")
    print(f"\nAll records are marked '{IMPORT_NOTE}'.")
    print("Open CardVault and review your inventory to add serial numbers,")
    print("set names, photos, and adjust any dates.\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        print("Usage:  python3 import_from_csv.py /path/to/csv/folder")
        sys.exit(1)

    run(sys.argv[1])
