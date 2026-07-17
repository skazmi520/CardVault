"""
CardVault v2 — Google Sheets-compatible CSV export.

Reproduces v1's export_to_csv.py sheet layout (PSA/BGS/CGC/TAG/SOLD/RAW) from
the v2 database, so the files can be dropped straight into Google Drive for
lookups when the laptop is off.

Column layout matches v1 exactly (import-compatible columns first, extended
columns after) with v2 additions (Year, Deal ID) appended at the end.
"""

import csv
import io
import zipfile
from datetime import date

from . import db as v2db

COMPANIES = ["PSA", "BGS", "CGC", "TAG"]


def _usd(val) -> str:
    if val is None or val == 0:
        return ""
    return f"${val:,.2f}"


def _cash_component(row) -> float:
    """v1 semantics: what was paid in cash, excluding trade value and fees."""
    acq_type = row["acquisition_type"] or "Cash"
    acq = row["acquisition_price"] or 0
    fee = row["grading_fee"] or 0
    trade = row["trade_value"] or 0
    if acq_type == "Trade":
        return 0.0
    if acq_type == "Cash & Trade":
        return max(0.0, acq - fee - trade)
    return max(0.0, acq - fee)


def graded_sheet(conn, company: str) -> tuple[str, int]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Card Name", "Grade", "Purchase Price", "Grading Fee",
        "Current Value", "Potential Profit/Loss", "Total Cost",
        "Serial Number", "Card Number", "Set Name", "Year",
        "Acquisition Type", "Trade Value", "Trade Details",
        "Acquisition Date", "Market Value Updated", "Notes",
    ])
    rows = conn.execute(
        "SELECT * FROM graded_cards WHERE status='active' AND grading_company=? "
        "ORDER BY card_name COLLATE NOCASE", (company,)).fetchall()
    for c in rows:
        market = c["market_value"] or 0
        total = c["acquisition_price"] or 0
        unknown = bool(c["basis_unknown"])
        pl = "" if unknown or not market else (market - total)
        w.writerow([
            c["card_name"], c["grade"],
            "unknown" if unknown else _usd(_cash_component(c)), _usd(c["grading_fee"]),
            _usd(market) if market else "", _usd(pl) if pl != "" else "",
            "unknown" if unknown else _usd(total),
            c["serial_number"] or "", c["card_number"] or "", c["set_name"] or "",
            c["year"] or "", c["acquisition_type"] or "Cash", _usd(c["trade_value"]),
            c["trade_details"] or "", c["acquisition_date"] or "",
            (c["market_value_updated"] or "")[:10], c["notes"] or "",
        ])
    return buf.getvalue(), len(rows)


def sold_sheet(conn) -> tuple[str, int]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Card Name", "Grade", "Purchase Price", "Grade Fee",
        "Total Cost", "Trade Value", "Cash Value", "Profit/Loss",
        "Company", "Serial Number", "Card Number", "Set Name", "Year",
        "Acquisition Type", "Trade Details",
        "Acquisition Date", "Sale Date", "Deal ID", "Notes",
    ])
    rows = conn.execute(
        "SELECT * FROM graded_cards WHERE status='disposed' OR is_sold=1 "
        "ORDER BY COALESCE(disposed_at, sale_date) DESC").fetchall()
    for c in rows:
        total = c["acquisition_price"] or 0
        proceeds = c["disposal_proceeds"]
        if proceeds is None:
            proceeds = c["sale_price"] or 0
        unknown = bool(c["basis_unknown"])
        gain = c["realized_gain"]
        if gain is None and not unknown:
            gain = proceeds - total
        acq_type = c["acquisition_type"] or "Cash"
        trade_val = c["trade_value"] or 0
        if acq_type == "Trade":
            cash_val = 0.0
        elif acq_type == "Cash & Trade":
            cash_val = max(0.0, proceeds - trade_val)
        else:
            cash_val, trade_val = proceeds, 0.0
        w.writerow([
            c["card_name"], c["grade"],
            "unknown" if unknown else _usd(_cash_component(c)), _usd(c["grading_fee"]),
            "unknown" if unknown else _usd(total),
            _usd(trade_val), _usd(cash_val),
            "unknown" if unknown else _usd(gain),
            c["grading_company"] or "", c["serial_number"] or "", c["card_number"] or "",
            c["set_name"] or "", c["year"] or "", acq_type, c["trade_details"] or "",
            c["acquisition_date"] or "",
            (c["disposed_at"] or c["sale_date"] or "")[:10],
            c["disposed_via_deal_id"] or "", c["notes"] or "",
        ])
    return buf.getvalue(), len(rows)


def raw_sheet(conn) -> tuple[str, int]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Card Name", "Purchase Price", "Current Value",
        "Expected Grade", "Graded Price", "At PSA?",
        "Card Number", "Set Name", "Year",
        "Acquisition Type", "Trade Value", "Trade Details",
        "Grading Status", "Target Company", "Purchase Date", "Notes",
    ])
    rows = conn.execute(
        "SELECT * FROM ungraded_cards WHERE status IN ('active','submitted_for_grading') "
        "ORDER BY card_name COLLATE NOCASE").fetchall()
    for c in rows:
        at_psa = "Yes" if (c["target_grading_company"] == "PSA"
                           and c["grading_status"] == "At Grading") else "No"
        w.writerow([
            c["card_name"], _usd(c["purchase_price"]), "", "", "", at_psa,
            c["card_number"] or "", c["set_name"] or "", c["year"] or "",
            c["acquisition_type"] or "Cash", _usd(c["trade_value"]),
            c["trade_details"] or "", c["grading_status"] or "",
            c["target_grading_company"] or "", c["purchase_date"] or "", c["notes"] or "",
        ])
    return buf.getvalue(), len(rows)


def build_zip(conn) -> tuple[bytes, dict]:
    """All sheets in one zip. Returns (zip_bytes, {sheet: row_count})."""
    sheets, counts = {}, {}
    for co in COMPANIES:
        content, n = graded_sheet(conn, co)
        if n:                      # skip graders with no active cards
            sheets[f"{co}.csv"] = content
            counts[co] = n
    sheets["SOLD.csv"], counts["SOLD"] = sold_sheet(conn)
    sheets["RAW.csv"], counts["RAW"] = raw_sheet(conn)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in sheets.items():
            z.writestr(name, content)
    return buf.getvalue(), counts


def zip_filename() -> str:
    return f"cardvault_sheets_{date.today().isoformat()}.zip"
