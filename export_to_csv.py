"""
CardVault Mac — CSV Exporter
============================
Exports all data to CSV files compatible with the original Google Sheets format.
Each file can be opened directly in Google Sheets or dropped into Google Drive.

Output files:
    PSA.csv, BGS.csv, CGC.csv, TAG.csv  — active (unsold) graded inventory
    SOLD.csv                             — all sold cards
    RAW.csv                              — ungraded cards

Additional columns beyond the original import format are included at the end
of each sheet so no existing data is lost on re-import.
"""

import csv
from pathlib import Path
from datetime import date
import database as db


def _fmt_usd(val) -> str:
    if val is None or val == 0:
        return ""
    return f"${val:,.2f}"


def _export_graded_sheet(output_dir: Path, company: str, cards: list) -> int:
    """Write one company's unsold graded cards to <company>.csv."""
    path = output_dir / f"{company}.csv"
    company_cards = [c for c in cards if c["grading_company"] == company]

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            # Original import-compatible columns
            "Card Name", "Grade", "Purchase Price", "Grading Fee",
            "Current Value", "Potential Profit/Loss", "Total Cost",
            # Extended columns
            "Serial Number", "Card Number", "Set Name",
            "Acquisition Type", "Trade Value", "Trade Details",
            "Acquisition Date", "Market Value Updated", "Notes",
        ])
        for c in company_cards:
            acq_type   = c["acquisition_type"] or "Cash"
            acq_price  = c["acquisition_price"] or 0
            fee        = c["grading_fee"] or 0
            trade_val  = c["trade_value"] or 0
            market     = c["market_value"] or 0
            total_cost = acq_price

            # Cash component depends on how the card was acquired:
            #   Cash       → everything is cash (minus grading fee)
            #   Trade      → no cash paid; cost basis = acquisition_price
            #   Cash&Trade → cash = acq_price - grading_fee - trade_value
            if acq_type == "Trade":
                purchase = 0.0
            elif acq_type == "Cash & Trade":
                purchase = max(0.0, acq_price - fee - trade_val)
            else:
                purchase = max(0.0, acq_price - fee)

            profit_loss = market - total_cost if market else ""
            writer.writerow([
                c["card_name"],
                c["grade"],
                _fmt_usd(purchase) if purchase else "",
                _fmt_usd(c["grading_fee"]),
                _fmt_usd(market) if market else "",
                _fmt_usd(profit_loss) if profit_loss != "" else "",
                _fmt_usd(total_cost),
                c["serial_number"] or "",
                c["card_number"] or "",
                c["set_name"] or "",
                c["acquisition_type"] or "Cash",
                _fmt_usd(c["trade_value"]),
                c["trade_details"] or "",
                c["acquisition_date"] or "",
                c["market_value_updated"] or "",
                c["notes"] or "",
            ])

    return len(company_cards)


def _export_sold_sheet(output_dir: Path, sold_cards: list) -> int:
    """Write all sold cards to SOLD.csv."""
    path = output_dir / "SOLD.csv"

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            # Original import-compatible columns
            "Card Name", "Grade", "Purchase Price", "Grade Fee",
            "Total Cost", "Trade Value", "Cash Value", "Profit/Loss",
            # Extended columns
            "Company", "Serial Number", "Card Number", "Set Name",
            "Acquisition Type", "Trade Details",
            "Acquisition Date", "Sale Date", "Notes",
        ])
        for c in sold_cards:
            acq_type   = c["acquisition_type"] or "Cash"
            acq_price  = c["acquisition_price"] or 0
            fee        = c["grading_fee"] or 0
            trade_val  = c["trade_value"] or 0
            total_cost = acq_price
            sale_price = c["sale_price"] or 0
            profit_loss = sale_price - total_cost

            if acq_type == "Trade":
                purchase = 0.0
            elif acq_type == "Cash & Trade":
                purchase = max(0.0, acq_price - fee - trade_val)
            else:
                purchase = max(0.0, acq_price - fee)

            # Split sale into cash vs trade components (best effort)
            if acq_type == "Trade":
                cash_val  = 0.0
            elif acq_type == "Cash & Trade":
                cash_val  = max(0.0, sale_price - trade_val)
            else:
                cash_val  = sale_price
                trade_val = 0.0

            writer.writerow([
                c["card_name"],
                c["grade"],
                _fmt_usd(purchase) if purchase else "",
                _fmt_usd(c["grading_fee"]),
                _fmt_usd(total_cost),
                _fmt_usd(trade_val),
                _fmt_usd(cash_val),
                _fmt_usd(profit_loss),
                c["grading_company"] or "",
                c["serial_number"] or "",
                c["card_number"] or "",
                c["set_name"] or "",
                acq_type,
                c["trade_details"] or "",
                c["acquisition_date"] or "",
                c["sale_date"] or "",
                c["notes"] or "",
            ])

    return len(sold_cards)


def _export_raw_sheet(output_dir: Path, ungraded_cards: list) -> int:
    """Write unconverted ungraded cards to RAW.csv."""
    path = output_dir / "RAW.csv"

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            # Original import-compatible columns
            "Card Name", "Purchase Price", "Current Value",
            "Expected Grade", "Graded Price", "At PSA?",
            # Extended columns
            "Card Number", "Set Name", "Year",
            "Acquisition Type", "Trade Value", "Trade Details",
            "Grading Status", "Target Company", "Purchase Date", "Notes",
        ])
        for c in ungraded_cards:
            at_psa = "Yes" if c["target_grading_company"] == "PSA" and c["grading_status"] == "At Grading" else "No"
            writer.writerow([
                c["card_name"],
                _fmt_usd(c["purchase_price"]),
                "",   # Current Value — not tracked on ungraded
                "",   # Expected Grade — stored in notes on import; no dedicated field
                "",   # Graded Price — same
                at_psa,
                c["card_number"] or "",
                c["set_name"] or "",
                c["year"] or "",
                c["acquisition_type"] or "Cash",
                _fmt_usd(c["trade_value"]),
                c["trade_details"] or "",
                c["grading_status"] or "",
                c["target_grading_company"] or "",
                c["purchase_date"] or "",
                c["notes"] or "",
            ])

    return len(ungraded_cards)


def run(output_folder: str) -> dict:
    """
    Export all data to CSVs in output_folder.
    Returns a summary dict: {company: count, ...}
    """
    db.init_db()
    output_dir = Path(output_folder).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    unsold  = db.get_graded_cards(sold=False)
    sold    = db.get_graded_cards(sold=True)
    ungraded = db.get_ungraded_cards(converted=False)

    summary = {}

    for company in ["PSA", "BGS", "CGC", "TAG"]:
        count = _export_graded_sheet(output_dir, company, unsold)
        summary[company] = count

    summary["SOLD"] = _export_sold_sheet(output_dir, sold)
    summary["RAW"]  = _export_raw_sheet(output_dir, ungraded)

    return summary


if __name__ == "__main__":
    import sys
    folder = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "Desktop" / "CardVault Export")
    summary = run(folder)
    print(f"\nCardVault Mac — Export Complete")
    print(f"Output: {folder}")
    print("=" * 40)
    for name, count in summary.items():
        print(f"  {name+'.csv':<12} → {count:>4} rows")
