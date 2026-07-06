"""
CardVault v2 — PSA public cert API with permanent cache and daily budget.

- Cert data never changes → every response is cached forever in
  psa_cert_cache and a cached cert is NEVER re-queried.
- Free tier is rate limited (~100/day) → psa_budget counts lookups per day
  (default budget 90, leaving headroom). When exhausted, lookups return
  status 'queued'; re-running the backfill the next day resumes them.
"""

import json
import urllib.error
import urllib.request
from datetime import date, datetime

DAILY_BUDGET = 90
ENDPOINT = "https://api.psacard.com/publicapi/cert/GetByCertNumber/{cert}"


def budget_used(conn) -> int:
    row = conn.execute("SELECT used FROM psa_budget WHERE day=?",
                       (date.today().isoformat(),)).fetchone()
    return row["used"] if row else 0


def budget_remaining(conn) -> int:
    return max(0, DAILY_BUDGET - budget_used(conn))


def _bump_budget(conn):
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO psa_budget (day, used) VALUES (?, 1) "
        "ON CONFLICT(day) DO UPDATE SET used = used + 1", (today,))
    conn.commit()


def normalize(cert_raw: dict) -> dict:
    """Map a PSACert payload onto card fields."""
    c = cert_raw.get("PSACert", cert_raw)
    brand = (c.get("Brand") or "").strip()
    set_name = brand
    if set_name.upper().startswith("POKEMON "):
        set_name = set_name[8:]
    set_name = set_name.title().replace("Tcg", "TCG")

    grade = str(c.get("CardGrade") or "").strip()      # e.g. "GEM MT 10", "EX 5"
    numeric = grade.split()[-1] if grade else ""

    subject = (c.get("Subject") or "").strip().title()

    return {
        "grading_company": "PSA",
        "cert_number": str(c.get("CertNumber") or "").strip(),
        "year": str(c.get("Year") or "").strip(),
        "set_name": set_name,
        "card_number": str(c.get("CardNumber") or "").strip(),
        "card_name": subject,
        "grade": numeric,
        "qualifier": (c.get("Variety") or "").strip(),
    }


def lookup_cert(conn, cert_number: str, token: str) -> dict:
    """Returns {status: cached|fetched|queued|not_found|error|no_token,
                data: normalized fields | None, raw: dict | None}"""
    cert_number = "".join(ch for ch in str(cert_number) if ch.isdigit())
    if not cert_number:
        return {"status": "error", "error": "no cert number", "data": None}

    row = conn.execute("SELECT response_json FROM psa_cert_cache WHERE cert_number=?",
                       (cert_number,)).fetchone()
    if row:
        raw = json.loads(row["response_json"])
        return {"status": "cached", "data": normalize(raw), "raw": raw}

    if not token:
        return {"status": "no_token", "data": None}
    if budget_remaining(conn) <= 0:
        return {"status": "queued", "data": None,
                "error": f"daily PSA budget ({DAILY_BUDGET}) exhausted — resumes tomorrow"}

    # Per PSA docs: header is  authorization: bearer <token>
    req = urllib.request.Request(
        ENDPOINT.format(cert=cert_number),
        headers={"authorization": "bearer " + token,
                 "Accept": "application/json",
                 "User-Agent": "CardVault/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # short-window throttle — call was refused, don't burn budget
            return {"status": "rate_limited", "data": None,
                    "error": "PSA API throttled (429) — will retry on next run"}
        _bump_budget(conn)                      # other failed calls still count
        if e.code == 500:
            # per PSA docs, 500 usually means invalid credentials
            return {"status": "error", "data": None,
                    "error": "PSA API 500 — usually invalid credentials; check PSA_API_TOKEN"}
        return {"status": "error", "error": f"PSA API {e.code}", "data": None}
    except Exception as e:
        return {"status": "error", "error": str(e), "data": None}

    _bump_budget(conn)
    # Per PSA docs, 200 does not mean data:
    #   IsValidRequest false            -> malformed cert number
    #   "No data found"                 -> valid format, no such cert
    #   "Request successful" + PSACert  -> real data
    msg = str(raw.get("ServerMessage", ""))
    if raw.get("IsValidRequest") is False:
        return {"status": "not_found", "data": None,
                "error": msg or "Invalid cert number", "raw": raw}
    c = raw.get("PSACert", {})
    if not c or not c.get("CertNumber"):
        return {"status": "not_found", "data": None,
                "error": msg or "No data found", "raw": raw}

    conn.execute(
        "INSERT OR REPLACE INTO psa_cert_cache (cert_number, fetched_at, response_json) "
        "VALUES (?,?,?)",
        (cert_number, datetime.now().isoformat(timespec="seconds"), json.dumps(raw)))
    conn.commit()
    return {"status": "fetched", "data": normalize(raw), "raw": raw}
