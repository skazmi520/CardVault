"""
CardVault v2 — slab photo pipeline: upload → extract → verify → match → review.

Nothing is ever written to a card without explicit per-field confirmation
from the review screen (photos.apply is only called by the review UI).
"""

import difflib
import json
from datetime import datetime
from pathlib import Path

from . import config
from . import db as v2db
from . import extraction, psa_api


# ── upload ──────────────────────────────────────────────────────────────────────

def save_upload(conn, file_storage) -> int:
    """Save one uploaded photo; create a pending photo_imports row."""
    v2db.SLAB_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(file_storage.filename or "photo.jpg").suffix.lower() or ".jpg"
    name = f"slab_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}"
    file_storage.save(v2db.SLAB_PHOTO_DIR / name)
    cur = conn.execute(
        "INSERT INTO photo_imports (file_path, uploaded_at) VALUES (?,?)",
        (name, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    return cur.lastrowid


# ── extraction + verification ───────────────────────────────────────────────────

def run_extract(conn, import_id: int) -> dict:
    """Extract label fields; if a PSA cert is found, verify against the PSA API
    (cache-first, budget-aware). Stores results on the photo_imports row."""
    row = conn.execute("SELECT * FROM photo_imports WHERE id=?", (import_id,)).fetchone()
    if row is None:
        raise ValueError(f"photo import {import_id} not found")

    keys = config.get_keys()
    path = v2db.SLAB_PHOTO_DIR / row["file_path"]

    try:
        ext = extraction.extract_label(path, keys["anthropic_api_key"])
    except RuntimeError as e:
        conn.execute("UPDATE photo_imports SET extract_error=? WHERE id=?",
                     (str(e), import_id))
        conn.commit()
        return {"ok": False, "error": str(e)}

    cert_norm, cert_status = None, None
    f = ext["fields"]
    if f.get("grading_company") == "PSA" and f.get("cert_number"):
        res = psa_api.lookup_cert(conn, f["cert_number"], keys["psa_api_token"])
        cert_status = res["status"]
        if res.get("data"):
            cert_norm = res["data"]

    conn.execute(
        """UPDATE photo_imports SET extracted_json=?, extract_error=NULL,
             extract_cost=?, cert_verified_json=?, status='extracted' WHERE id=?""",
        (json.dumps({"fields": f, "low_confidence": ext["low_confidence"]}),
         ext["cost"],
         json.dumps(cert_norm) if cert_norm else None,
         import_id))
    conn.commit()

    match = find_match(conn, f, cert_norm)
    if match["exact"]:
        conn.execute("UPDATE photo_imports SET matched_table='graded_cards', matched_id=? "
                     "WHERE id=?", (match["exact"]["id"], import_id))
        conn.commit()

    return {"ok": True, "fields": f, "low_confidence": ext["low_confidence"],
            "cost": ext["cost"], "cert_status": cert_status,
            "cert_data": cert_norm, "match": match}


# ── matching ────────────────────────────────────────────────────────────────────

def find_match(conn, extracted: dict, cert_data: dict | None = None) -> dict:
    """Cert exact match first, then fuzzy on name + grade (+set boost).
    Returns {"exact": card|None, "candidates": [top 3 scored]}."""
    best_cert = (cert_data or {}).get("cert_number") or extracted.get("cert_number") or ""
    best_cert = "".join(ch for ch in best_cert if ch.isalnum())

    cards = conn.execute(
        "SELECT id, card_name, set_name, card_number, year, grading_company, "
        "grade, serial_number FROM graded_cards WHERE status='active'").fetchall()

    if best_cert:
        for c in cards:
            if "".join(ch for ch in (c["serial_number"] or "") if ch.isalnum()) == best_cert:
                return {"exact": dict(c), "candidates": []}

    name = (extracted.get("card_name") or "").lower()
    grade = (extracted.get("grade") or "").strip()
    sset = (extracted.get("set_name") or "").lower()
    if not name:
        return {"exact": None, "candidates": []}

    scored = []
    for c in cards:
        s = difflib.SequenceMatcher(None, name, (c["card_name"] or "").lower()).ratio()
        if grade and str(c["grade"]).strip() == grade:
            s += 0.15
        if sset and sset in (c["set_name"] or "").lower():
            s += 0.10
        if s >= 0.55:
            scored.append((round(s, 3), dict(c)))
    scored.sort(key=lambda t: -t[0])
    return {"exact": None,
            "candidates": [dict(c, _score=s) for s, c in scored[:3]]}


# ── apply / reject ──────────────────────────────────────────────────────────────

_APPLY_FIELDS = {"grading_company", "grade", "serial_number", "card_name",
                 "set_name", "card_number", "year"}


def apply_to_card(conn, import_id: int, card_id: int | None, fields: dict) -> int:
    """Write accepted fields to an existing card (card_id) or create a new
    graded card (card_id None). Links the photo import and marks it applied."""
    fields = {k: str(v).strip() for k, v in fields.items() if k in _APPLY_FIELDS}

    if card_id is None:
        cur = conn.execute(
            """INSERT INTO graded_cards
                 (serial_number, grading_company, grade, card_name, card_number,
                  set_name, year, acquisition_type, acquisition_price,
                  acquisition_date, notes, date_added, status)
               VALUES (?,?,?,?,?,?,?, 'Cash', 0, ?, 'Created from slab photo', ?, 'active')""",
            (fields.get("serial_number", ""), fields.get("grading_company", ""),
             fields.get("grade", ""), fields.get("card_name", "Unknown card"),
             fields.get("card_number", ""), fields.get("set_name", ""),
             fields.get("year", ""), datetime.now().date().isoformat(),
             datetime.now().isoformat()))
        card_id = cur.lastrowid
    elif fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE graded_cards SET {sets} WHERE id=?",
                     [*fields.values(), card_id])

    conn.execute(
        "UPDATE photo_imports SET status='applied', matched_table='graded_cards', "
        "matched_id=? WHERE id=?", (card_id, import_id))
    conn.commit()
    return card_id


def reject(conn, import_id: int):
    conn.execute("UPDATE photo_imports SET status='rejected' WHERE id=?", (import_id,))
    conn.commit()


# ── backfill helpers ────────────────────────────────────────────────────────────

def incomplete_cards(conn) -> list[dict]:
    """Active graded cards missing cert, set, card number, or year."""
    out = []
    for r in conn.execute("SELECT * FROM graded_cards WHERE status='active' "
                          "ORDER BY card_name COLLATE NOCASE"):
        missing = [label for label, col in
                   [("cert", "serial_number"), ("set", "set_name"),
                    ("card #", "card_number"), ("year", "year")]
                   if not (r[col] or "").strip()]
        if missing:
            out.append({"id": r["id"], "name": r["card_name"],
                        "company": r["grading_company"], "grade": r["grade"],
                        "set": r["set_name"] or "", "number": r["card_number"] or "",
                        "year": r["year"] or "", "cert": r["serial_number"] or "",
                        "missing": missing})
    return out


def list_imports(conn, status: str | None = None):
    if status:
        return conn.execute("SELECT * FROM photo_imports WHERE status=? ORDER BY id DESC",
                            (status,)).fetchall()
    return conn.execute("SELECT * FROM photo_imports WHERE status != 'rejected' "
                        "ORDER BY id DESC").fetchall()
