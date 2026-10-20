"""
CSV import: bring in cards tracked somewhere else (a spreadsheet, a reshaped
Collectr export). Two kinds: slabs and raw cards.

Nothing here trusts the browser. preview() parses and validates; apply()
re-parses the same text itself, refuses unless every row is clean, and writes
the lot in ONE transaction, so a failure part-way leaves nothing behind.
"""

import csv
import io
import re
from datetime import date, datetime

from . import cards as v2cards
from . import db as v2db
from .money import parse_money

# (header, key, required) in the order the template lists them
SLAB_COLUMNS = [
    ("Card name", "name", True), ("Set", "set", False),
    ("Card number", "number", False), ("Year", "year", False),
    ("Grader", "grader", True), ("Grade", "grade", True),
    ("Cert", "cert", False), ("Cost", "cost", False),
    ("Market value", "market", False), ("Date acquired", "acquired", False),
    ("Personal collection", "pc", False), ("Notes", "card_notes", False),
]
RAW_COLUMNS = [
    ("Card name", "name", True), ("Set", "set", False),
    ("Card number", "number", False), ("Year", "year", False),
    ("Cost", "cost", False), ("Market value", "market", False),
    ("Grading status", "grading_status", False),
    ("Date acquired", "acquired", False),
    ("Personal collection", "pc", False), ("Notes", "card_notes", False),
]
COLUMNS = {"slabs": SLAB_COLUMNS, "raw": RAW_COLUMNS}

GRADERS = ("PSA", "BGS", "CGC", "TAG")
_GRADING = {"not slated": "Not Slated", "slated": "Slated", "at grading": "At Grading"}
_YES = {"yes", "y", "true", "x", "1"}
_NO = {"", "no", "n", "false", "0"}
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_US = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})$")


def header_line(kind: str) -> str:
    return ",".join(h for h, _, _ in _cols(kind)) + "\r\n"


def _cols(kind: str):
    if kind not in COLUMNS:
        raise ValueError("kind must be slabs or raw")
    return COLUMNS[kind]


def _parse_date(text: str) -> str:
    t = text.strip()
    if not t:
        return date.today().isoformat()
    m = _ISO.match(t)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = _US.match(t)
        if not m:
            raise ValueError(f"Date acquired: {text!r} is not YYYY-MM-DD or M/D/YYYY")
        mo, d, y = int(m[1]), int(m[2]), int(m[3])
        if len(m[3]) == 2:
            y += 2000
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        raise ValueError(f"Date acquired: {text!r} is not a real date") from None


def _parse_row(kind: str, cell: dict) -> tuple[dict, list, list]:
    """Values and error messages for one row. Every problem is reported, not
    just the first, so one preview shows everything to fix."""
    errors, notes = [], []
    out = {k: (cell.get(k) or "").strip() for _, k, _ in _cols(kind)}

    if not out["name"]:
        errors.append("Card name is required")

    if kind == "slabs":
        g = out["grader"].upper()
        if g in GRADERS:
            out["grader"] = g
        else:
            errors.append(f"Grader {out['grader']!r} must be PSA, BGS, CGC or TAG"
                          if out["grader"] else "Grader is required (PSA, BGS, CGC or TAG)")
        if not out["grade"]:
            errors.append("Grade is required")
    else:
        gs = _GRADING.get(out["grading_status"].lower()) if out["grading_status"] else "Not Slated"
        if gs is None:
            errors.append(f"Grading status {out['grading_status']!r} must be "
                          "Not Slated, Slated or At Grading")
        out["grading_status"] = gs or out["grading_status"]

    cost_bad = False
    for key, label in (("cost", "Cost"), ("market", "Market value")):
        try:
            out[key] = parse_money(out[key], label)
        except ValueError as e:
            errors.append(str(e))
            out[key] = None
            cost_bad = cost_bad or key == "cost"
    if out["cost"] is None and not cost_bad:
        notes.append("no cost: counted in inventory, left out of profit numbers")

    try:
        out["acquired"] = _parse_date(out["acquired"])
    except ValueError as e:
        errors.append(str(e))

    pc = out["pc"].lower()
    if pc in _YES:
        out["pc"] = True
    elif pc in _NO:
        out["pc"] = False
    else:
        errors.append(f"Personal collection {out['pc']!r} must be yes or no")
        out["pc"] = False
    return out, errors, notes


def _parse(conn, kind: str, text: str) -> dict:
    cols = _cols(kind)
    file_errors, file_notes, rows = [], [], []
    text = (text or "").lstrip("﻿")
    try:
        grid = list(csv.reader(io.StringIO(text)))
    except csv.Error as e:
        return {"kind": kind, "rows": [], "file_errors": [f"Could not read this file as CSV: {e}"],
                "notes": []}
    if not grid or not any(c.strip() for c in grid[0]):
        return {"kind": kind, "rows": [], "file_errors": ["The file is empty or has no header row"],
                "notes": []}

    known = {h.lower(): k for h, k, _ in cols}
    index, extra = {}, []
    for i, h in enumerate(grid[0]):
        h = h.strip().lower()
        if h in known:
            index.setdefault(known[h], i)
        elif h:
            extra.append(grid[0][i].strip())
    for h, k, required in cols:
        if required and k not in index:
            file_errors.append(f"Missing required column: {h}")
    if extra:
        file_notes.append("Ignored columns: " + ", ".join(extra))

    if file_errors:
        return {"kind": kind, "rows": [], "file_errors": file_errors, "notes": file_notes}

    if kind == "slabs":
        graded_certs = {r[0] for r in conn.execute(
            "SELECT TRIM(serial_number) FROM graded_cards WHERE TRIM(serial_number) != ''")}
    else:
        graded_certs = set()
    raw_certs = {r[0] for r in conn.execute(
        "SELECT TRIM(psa_cert) FROM ungraded_cards WHERE psa_cert IS NOT NULL "
        "AND TRIM(psa_cert) != ''")}
    held = graded_certs | raw_certs
    seen = {}

    for n, cells in enumerate(grid[1:], start=2):
        if not any(c.strip() for c in cells):
            continue
        cell = {k: (cells[i] if i < len(cells) else "") for k, i in index.items()}
        vals, errors, notes = _parse_row(kind, cell)
        dup = False
        cert = vals.get("cert", "")
        if cert:
            if cert in seen:
                errors.append(f"Cert {cert} already appears on row {seen[cert]} of this file")
            else:
                seen[cert] = n
                if cert in held:
                    dup = True
                    notes.append("cert already in your collection")
        status = "error" if errors else ("duplicate" if dup else "ok")
        rows.append({"line": n, **vals, "errors": errors, "notes": notes, "status": status})

    if not rows:
        file_errors.append("No card rows found below the header")
    return {"kind": kind, "rows": rows, "file_errors": file_errors, "notes": file_notes}


def preview(conn, kind: str, text: str) -> dict:
    res = _parse(conn, kind, text)
    counts = {"ok": 0, "error": 0, "duplicate": 0}
    for r in res["rows"]:
        counts[r["status"]] += 1
    res["counts"] = counts
    return res


def apply(conn, kind: str, text: str) -> dict:
    res = preview(conn, kind, text)
    if res["file_errors"]:
        raise ValueError("; ".join(res["file_errors"]))
    bad = res["counts"]["error"]
    if bad:
        raise ValueError(f"Fix the {bad} row{'s' if bad != 1 else ''} with problems and preview again")

    created = 0
    try:
        for r in res["rows"]:
            if r["status"] != "ok":
                continue
            common = dict(card_name=r["name"], basis=r["cost"], market_value=r["market"],
                          acquisition_date=r["acquired"], card_number=r["number"],
                          set_name=r["set"], year=r["year"], notes=r["card_notes"],
                          is_pc=r["pc"], source="import", _commit=False)
            if kind == "slabs":
                v2cards.create_card(conn, "graded_cards", grading_company=r["grader"],
                                    grade=r["grade"], serial_number=r["cert"], **common)
            else:
                cid = v2cards.create_card(conn, "ungraded_cards",
                                          grading_status=r["grading_status"], **common)
                if r["grading_status"] == "At Grading":
                    # the same end state as cards.bulk_grading_update, inside this transaction
                    v2db.update_card(
                        conn, "ungraded_cards", cid,
                        {"status": "submitted_for_grading",
                         "submitted_at": datetime.now().isoformat(timespec="seconds")},
                        "import")
            created += 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"created": created, "skipped": res["counts"]["duplicate"]}
