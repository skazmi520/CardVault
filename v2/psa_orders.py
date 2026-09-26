"""
CardVault v2 — PSA order CSV import.

PSA hands you a CSV per submission. Two shapes:

  open order       Cert #, Type, Description, Grade(blank), After Service
  completed order  … Grade("MINT 9"), After Service("Shipped to you"), Images

Both are authoritative about what is physically at PSA, which the app
otherwise only knows from a per-card dropdown nobody updates after shipping a
40-card batch. Importing an open order marks the batch At Grading and records
the cert up front; importing a completed order records the real grades.

Matching is cert-first (exact), then description. PSA descriptions are
"YEAR BRAND … NUMBER NAME-QUALIFIER", e.g.

  1999 POKEMON GAME 4 CHARIZARD-HOLO
  2021 POKEMON SWORD & SHIELD EVOLVING SKIES 214 FULL ART/UMBREON VMAX SECRET

Note "POKEMON GAME" is how PSA writes Base Set, so set-name matching is
actively misleading on vintage — the reliable signals are card name, card
number and year.
"""

import csv
import io
import re
from datetime import datetime

from . import db as v2db

# words that carry no identifying signal in a PSA description
_NOISE = {
    "pokemon", "card", "holo", "reverse", "foil", "full", "art", "secret",
    "rare", "ultra", "promo", "black", "star", "edition", "1st", "first",
    "illustration", "collection", "series", "japanese", "english", "en",
    "game", "the", "and", "vol", "set", "sv", "ex-holo",
}

_GRADE_WORDS = {
    "gem mt": 10, "gem mint": 10, "mint": 9, "nm-mt": 8, "near mint-mint": 8,
    "nm": 7, "near mint": 7, "ex-mt": 6, "excellent-mint": 6,
    "ex": 5, "excellent": 5, "vg-ex": 4, "vg": 3, "good": 2, "poor": 1,
}


# PSA "no grade" reason codes — these come back as N#: REASON with no numeric
# grade. The card is slabbed but ungraded, and it is emphatically not a 0:
# treating it as one would put a false number in the collection.
_NO_GRADE = re.compile(r"^\s*(N\d)\s*[:\-]?\s*(.*)$", re.I)


def parse_grade(raw: str) -> dict:
    """Parse PSA's grade text.

    Returns {grade, label, no_grade}. 'MINT 9' -> grade '9'.
    'N5: ALTERED STOCK' -> no_grade True, grade '' — an N-code is a refusal to
    grade, so there is no number to record and the card must not be promoted
    as if there were.
    """
    raw = (raw or "").strip()
    if not raw:
        return {"grade": "", "label": "", "no_grade": False}
    nm = _NO_GRADE.match(raw)
    if nm and nm.group(2):
        return {"grade": "", "label": raw, "no_grade": True}
    m = re.search(r"(\d+(?:\.\d+)?)\s*$", raw)
    if m:
        return {"grade": m.group(1), "label": raw, "no_grade": False}
    low = raw.lower()
    for word, num in _GRADE_WORDS.items():
        if low.startswith(word):
            return {"grade": str(num), "label": raw, "no_grade": False}
    return {"grade": "", "label": raw, "no_grade": True}


def parse_description(desc: str) -> dict:
    """Pull year, card number and name tokens out of a PSA description."""
    desc = (desc or "").strip()
    year = ""
    m = re.match(r"\s*(\d{4})\b", desc)
    if m:
        year = m.group(1)
        desc = desc[m.end():]

    # card number: last standalone number that still has words after it
    number = ""
    for m in re.finditer(r"\b([A-Z]{0,3}\d{1,4}[A-Z]?)\b", desc):
        tail = desc[m.end():].strip()
        if tail and re.search(r"[A-Za-z]{3}", tail):
            number = m.group(1)
            name_part = tail
    if not number:
        name_part = desc

    name_part = name_part.split("/")[-1]          # "FULL ART/UMBREON VMAX" -> "UMBREON VMAX"
    name_part = re.sub(r"-(HOLO|REVERSE FOIL|FOIL)\b", " ", name_part, flags=re.I)
    tokens = {w for w in re.findall(r"[a-z0-9']+", name_part.lower())
              if w not in _NOISE and len(w) > 2}
    return {"year": year, "number": number.lstrip("0") or number,
            "name_tokens": tokens, "name_text": name_part.strip()}


def read_order(text: str) -> list[dict]:
    """Parse a PSA order CSV into rows."""
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        cert = (r.get("Cert #") or r.get("﻿Cert #") or "").strip()
        if not cert:
            continue
        g = parse_grade(r.get("Grade"))
        rows.append({
            "cert": cert,
            "description": (r.get("Description") or "").strip(),
            "grade": g["grade"],
            "grade_label": g["label"],
            "no_grade": g["no_grade"],
            "after_service": (r.get("After Service") or "").strip(),
            "images": (r.get("Images") or "").strip(),
            **parse_description(r.get("Description")),
        })
    return rows


def _candidates(conn):
    """Raw cards that could plausibly be in a submission."""
    return conn.execute(
        "SELECT id, card_name, set_name, card_number, year, purchase_price, "
        "grading_status, status FROM ungraded_cards "
        "WHERE status IN ('active','submitted_for_grading')").fetchall()


def match_order(conn, rows: list[dict]) -> dict:
    """Pair CSV rows with raw cards. Cert match wins outright; otherwise score
    on card name overlap, confirmed by card number and year."""
    known_certs = {
        (r["serial_number"] or "").strip(): r
        for r in conn.execute(
            "SELECT id, serial_number, card_name, grade, status FROM graded_cards "
            "WHERE TRIM(COALESCE(serial_number,'')) <> ''")}

    cards = _candidates(conn)
    # a cert already recorded against a raw card is spoken for — without this
    # the same cert can be written onto two different cards across imports
    taken = {(r["psa_cert"] or "").strip()
             for r in conn.execute("SELECT psa_cert FROM ungraded_cards "
                                   "WHERE TRIM(COALESCE(psa_cert,'')) <> ''")}
    assigned = {r["id"] for r in conn.execute(
        "SELECT id FROM ungraded_cards WHERE TRIM(COALESCE(psa_cert,'')) <> ''")}
    used: set[int] = set(assigned)
    matched, unmatched, already = [], [], []

    for row in rows:
        if row["cert"] in known_certs:
            already.append({"row": row, "card": dict(known_certs[row["cert"]])})
            continue
        if row["cert"] in taken:
            hit = conn.execute("SELECT id, card_name, set_name FROM ungraded_cards "
                               "WHERE psa_cert=?", (row["cert"],)).fetchone()
            already.append({"row": row, "card": {
                "id": hit["id"], "card_name": hit["card_name"],
                "grade": "at grading", "status": "cert already recorded"}})
            continue

        best, best_score = None, 0.0
        for c in cards:
            if c["id"] in used:
                continue
            name_toks = {w for w in re.findall(r"[a-z0-9']+", (c["card_name"] or "").lower())
                         if w not in _NOISE and len(w) > 2}
            if not name_toks:
                continue
            overlap = len(name_toks & row["name_tokens"])
            if not overlap:
                continue
            score = 5.0 * overlap / len(name_toks)
            # A shared name alone is weak evidence — "Rayquaza C Lv.X" (2009 #146)
            # and "Rayquaza VMAX" (2021 #218) share exactly one token. Card
            # number and year must be able to REFUTE a match, not just confirm
            # one, or a single-word name promotes the wrong card.
            cn = (c["card_number"] or "").strip().lstrip("0")
            if cn and row["number"]:
                if cn.lower() == row["number"].lower():
                    score += 3
                else:
                    score -= 4
            cy = (c["year"] or "").strip()
            if cy and row["year"]:
                if cy == row["year"]:
                    score += 2
                else:
                    score -= 3
            if score > best_score:
                best, best_score = c, score
        if best is not None and best_score >= 3.0:
            used.add(best["id"])
            matched.append({"row": row, "card": dict(best), "score": round(best_score, 1)})
        else:
            unmatched.append({"row": row})

    return {"matched": matched, "unmatched": unmatched, "already": already,
            "total": len(rows)}


def apply_order(conn, rows: list[dict], pairs: list[dict], *,
                order_id: str = "", grading_cost_each: float = 0.0) -> dict:
    """Apply an import. pairs is [{cert, card_id}] confirmed by the user.

    A row carrying a grade is a completed submission: the card is promoted to
    the graded collection with its real grade and cert. A row with no grade is
    still at PSA: the card is marked At Grading with the cert recorded, so the
    return can be matched later without guessing.

    All or nothing: the whole order is one transaction. A failure on any pair
    rolls back every card already touched and re-raises, so a 40-card import
    never lands half-applied with no report of where it stopped.
    """
    from . import cards as v2cards

    by_cert = {r["cert"]: r for r in rows}
    promoted, submitted, skipped = [], [], []

    try:
        for p in pairs:
            row = by_cert.get(str(p["cert"]))
            cid = int(p["card_id"])
            if row is None:
                skipped.append({"cert": p.get("cert"), "why": "cert not in this order"})
                continue
            raw = conn.execute(
                "SELECT card_name, status, psa_cert FROM ungraded_cards WHERE id=?",
                (cid,)).fetchone()
            if raw is None or raw["status"] not in ("active", "submitted_for_grading"):
                skipped.append({"cert": row["cert"], "why": "card no longer active"})
                continue
            if raw["psa_cert"] and str(raw["psa_cert"]) != str(row["cert"]):
                # never overwrite a cert already recorded on the card
                skipped.append({"cert": row["cert"], "name": raw["card_name"],
                                "why": f"card already carries cert {raw['psa_cert']}"})
                continue

            if row.get("no_grade"):
                # PSA refused to grade it. Record the outcome on the raw card
                # rather than inventing a number; the card comes back in a holder
                # but has no grade to promote.
                note = f"PSA {row['grade_label']} (cert {row['cert']})"
                cur_notes = conn.execute("SELECT notes FROM ungraded_cards WHERE id=?",
                                         (cid,)).fetchone()["notes"] or ""
                v2db.update_card(conn, "ungraded_cards", cid,
                                 {"grading_status": "Not Slated", "status": "active",
                                  "psa_cert": row["cert"], "psa_order": order_id,
                                  "notes": " | ".join(x for x in (cur_notes.strip(), note) if x)},
                                 "import", f"psa-order:{order_id}")
                skipped.append({"cert": row["cert"], "name": raw["card_name"],
                                "why": row["grade_label"]})
                continue
            if row["grade"]:
                gid = v2cards.promote_raw(
                    conn, cid, grading_company="PSA", grade=row["grade"],
                    serial_number=row["cert"], grading_cost=grading_cost_each,
                    _commit=False)
                promoted.append({"cert": row["cert"], "name": raw["card_name"],
                                 "grade": row["grade"], "graded_id": gid})
            else:
                sub = conn.execute("SELECT submitted_at FROM ungraded_cards WHERE id=?",
                                   (cid,)).fetchone()["submitted_at"]
                v2db.update_card(conn, "ungraded_cards", cid,
                                 {"grading_status": "At Grading",
                                  "status": "submitted_for_grading",
                                  "target_grading_company": "PSA",
                                  "submitted_at": sub or datetime.now().isoformat(timespec="seconds"),
                                  "psa_cert": row["cert"], "psa_order": order_id},
                                 "import", f"psa-order:{order_id}")
                submitted.append({"cert": row["cert"], "name": raw["card_name"]})
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return {"promoted": promoted, "submitted": submitted, "skipped": skipped}
