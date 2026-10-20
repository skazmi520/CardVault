"""
CardVault v2 — local web app.

Run:  python3 -m v2.app          (serves http://127.0.0.1:5177)

The v2 startup guard runs before the server binds: if the v2 database is
missing or unmarked, the app refuses to start. v1 is never opened for writing.
"""

import json
import threading
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from flask import (Flask, abort, g, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)

from . import cards as v2cards
from . import config
from . import db as v2db
from . import psa_api
from . import psa_orders
from .deals import (CardIn, CardOut, LaterChangesError, add_deal_photo, edit_deal,
                    get_deal, list_deals, save_deal)

app = Flask(__name__)

_MONEY_RE = __import__("re").compile(r"^-?\$?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?$")


def _parse_money(v, field: str, *, allow_negative: bool = False):
    """A money value exactly as typed, or None when left blank. Anything that is
    not plainly a number ("3.5k", "1 200", "35O0", inf, nan) is refused rather
    than truncated: a value the user typed is either stored as typed or rejected
    so he can see it."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    if isinstance(v, bool):
        raise ValueError(f"{field} must be a number")
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        t = str(v).strip()
        if not _MONEY_RE.match(t) or not any(ch.isdigit() for ch in t):
            raise ValueError(f"{field}: {v!r} is not a plain amount")
        x = float(t.replace("$", "").replace(",", ""))
    if x != x or x in (float("inf"), float("-inf")) or abs(x) > 1e9:
        raise ValueError(f"{field} must be a real amount")
    if x < 0 and not allow_negative:
        raise ValueError(f"{field} cannot be negative")
    return round(x, 2)


def _card_ref(p: dict, id_key: str = "id"):
    """(table, id) from a request, both required and well-formed. No defaults:
    a request that doesn't say which card it means changes nothing."""
    table = p.get("table")
    if table not in ("graded_cards", "ungraded_cards"):
        raise ValueError("table must be graded_cards or ungraded_cards")
    cid = p.get(id_key)
    if isinstance(cid, bool) or not isinstance(cid, (int, str)):
        raise ValueError(f"{id_key} must be an integer")
    cid = int(cid)
    if cid <= 0:
        raise ValueError(f"{id_key} must be positive")
    return table, cid
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024   # 32 MB uploads


@app.template_filter("money")
def _money(v):
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


@app.template_filter("money0")
def _money0(v):
    """Whole dollars — for headline figures where cents are noise."""
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}"


@app.template_filter("gain0")
def _gain0(v):
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.0f}"


@app.template_filter("mdy")
def _mdy(v):
    """'2026-09-02...' -> 'Sep 2'."""
    try:
        return date.fromisoformat(str(v)[:10]).strftime("%b %-d")
    except (ValueError, TypeError):
        return str(v or "")[:10]


@app.template_filter("month")
def _month(v):
    """'2026-09' -> 'September 2026'."""
    try:
        return date.fromisoformat(str(v)[:7] + "-01").strftime("%B %Y")
    except (ValueError, TypeError):
        return str(v or "")


@app.template_filter("gain")
def _gain(v):
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.2f}"


def conn():
    """Per-request connection. Closed (with rollback on error) in teardown, so
    an exception mid-endpoint can never leak a connection holding an open write
    transaction — which locks the whole database for every other writer."""
    if "db" not in g:
        g.db = v2db.get_connection()
    return g.db


@app.teardown_appcontext
def _close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        try:
            if exc is not None:
                db.rollback()
            db.close()
        except Exception:
            pass


# Bad input is the caller's fault, not a crash. The model layer raises
# ValueError deliberately for validation ("card_name is required", "only active
# cards can leave in a deal"), and a missing or mistyped field surfaces as
# KeyError or TypeError from p["id"] / int(...).
_CLIENT_ERRORS = (ValueError, KeyError, TypeError)


@app.errorhandler(Exception)
def _api_errors(e):
    """API callers get JSON errors, never an HTML 500 page. Non-API routes and
    deliberate HTTP errors (404s etc.) pass through untouched.

    Malformed input returns 400 rather than 500. Twelve endpoints were throwing
    500s on a missing field — /api/card/create, /api/card/remove, /api/pc and
    the rest — because only a handful wrapped their own validation. A route
    fuzz run found every one of them. A 500 tells the user "something broke";
    a 400 tells them what was wrong, and keeps genuine faults visible instead
    of hiding them in a crowd of validation noise."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    if request.path.startswith("/api/"):
        if isinstance(e, _CLIENT_ERRORS):
            msg = str(e) or type(e).__name__
            if isinstance(e, KeyError):
                msg = f"missing required field {msg}"
            return jsonify({"ok": False, "error": msg}), 400
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    raise e


_WRITES = ("POST", "PUT", "PATCH", "DELETE")


@app.before_request
def _same_origin_writes():
    """Only this app's own pages can change the books. A browser always sends
    Origin (or at least Referer) on a cross-site POST, so a page elsewhere that
    posts to 127.0.0.1:5177 is refused here. Requiring a JSON body closes the
    one kind of cross-site request that needs no preflight: a plain form post.
    Photo uploads are multipart and pass the second check, never the first."""
    if request.method not in _WRITES:
        return None
    src = request.headers.get("Origin") or request.headers.get("Referer")
    if src is not None and urlsplit(src).netloc.lower() != request.host.lower():
        return jsonify({"ok": False, "error": "refused: request came from another site"}), 403
    if request.mimetype != "multipart/form-data" and not request.is_json:
        return jsonify({"ok": False, "error": "send the request body as JSON"}), 415
    return None


_backup_lock = threading.Lock()


@app.before_request
def _daily_backup():
    """The startup backup covers a day only if the app is restarted that day.
    Left running through a show weekend, the first request of each new day
    takes the snapshot instead, off the request thread, errors swallowed.
    Only armed by main(): a test client never writes to the backup folders."""
    last = app.config.get("LAST_BACKUP")
    today = date.today()
    if last is None or last == today:
        return None
    with _backup_lock:
        if app.config.get("LAST_BACKUP") == today:
            return None
        app.config["LAST_BACKUP"] = today

    def run():
        try:
            v2db.backup_v2()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()
    return None


# ── pages ───────────────────────────────────────────────────────────────────────

def _brief(x):
    slab = x["_table"] == "graded_cards"
    return {"name": x["card_name"], "company": x.get("grading_company") if slab else "",
            "grade": x.get("grade") if slab else "", "kind": "slab" if slab else "raw"}


def _deal_row(c, d):
    """A deal as a list row: the cards that moved lead, because the owner
    remembers the cards, not the counterparty."""
    det = get_deal(c, d["id"])
    return {
        "id": d["id"], "occurred_at": d["occurred_at"],
        "counterparty": d["counterparty"], "location": d["location"],
        "method": d["payment_method"], "cash": d["cash_amount"],
        "n_out": len(det["cards_out"]), "n_in": len(det["cards_in"]),
        "out": [_brief(x) for x in det["cards_out"]], "in": [_brief(x) for x in det["cards_in"]],
        "gain": round(sum((x["realized_gain"] or 0) for x in det["cards_out"]), 2),
        "unreconciled": "[unreconciled]" in (d["notes"] or ""),
    }


@app.get("/")
def dashboard():
    c = conn()
    v2cards.record_snapshot(c)
    stats = v2cards.dashboard_stats(c)
    extras = v2cards.dashboard_extras(c)
    snaps = v2cards.snapshots(c)
    recent = [_deal_row(c, d) for d in list_deals(c)[:6]]
    tstats = v2cards.trade_stats(c)
    movers = v2cards.top_movers(c)
    grading = v2cards.at_grading(c)
    sell = v2cards.sell_candidates(c)
    psa = v2cards.psa_capital(c)
    c.close()
    if extras.get("delta") and snaps and len(snaps) >= 2 and snaps[-2]["value"]:
        extras["delta"]["pct"] = round(extras["delta"]["amount"] / snaps[-2]["value"] * 100, 1)
    elif extras.get("delta"):
        extras["delta"]["pct"] = None
    days_out = [g["days"] for g in grading if g["days"] is not None]
    return render_template("dashboard.html", active="dashboard",
                           stats=stats, extras=extras, tstats=tstats,
                           movers=movers, grading=grading, sell=sell,
                           psa=psa, snaps=snaps, recent=recent,
                           today_label=date.today().strftime("%A, %B %-d"),
                           year=date.today().year,
                           grading_oldest=(max(days_out) if days_out else None))


@app.get("/collection")
def collection():
    c = conn()
    cards = v2cards.list_cards(c, include_gone=True)
    sets = sorted({x["set"] for x in cards if x["set"]}, key=str.lower)
    c.close()
    return render_template("collection.html", active="collection",
                           cards=cards, sets=sets,
                           lost_reasons=v2db.LOST_REASONS)


@app.get("/deals")
def deals_page():
    c = conn()
    today = date.today().isoformat()
    rows = []
    cash_in = cash_out = day_gain = 0.0
    today_rows = []
    for d in list_deals(c):
        row = _deal_row(c, d)
        rows.append(row)
        if d["occurred_at"][:10] == today:
            today_rows.append(row)
            if d["cash_amount"] > 0:
                cash_in += d["cash_amount"]
            else:
                cash_out += -d["cash_amount"]
            day_gain += row["gain"]
    c.close()
    # the list groups by day — deals cluster on show days, and a day header
    # with its count and net cash is how the owner thinks about a show
    days = []
    for r in rows:
        key = r["occurred_at"][:10]
        if not days or days[-1]["date"] != key:
            days.append({"date": key, "rows": [], "net": 0.0, "gain": 0.0})
        days[-1]["rows"].append(r)
        days[-1]["net"] += r["cash"]
        days[-1]["gain"] += r["gain"]
    return render_template("deals.html", active="deals", deals=rows, days=days,
                           today=today, today_rows=today_rows,
                           cash_in=round(cash_in, 2), cash_out=round(cash_out, 2),
                           day_gain=round(day_gain, 2))


@app.get("/deals/new")
def deal_new():
    c = conn()
    # only status='active': the deal engine refuses at-grading cards (they are
    # physically at the grader and cannot be handed across a table)
    cards = [x for x in v2cards.list_cards(c) if x["status"] == "active"]
    c.close()
    return render_template("deal_new.html", active="deals",
                           cards=cards, methods=v2db.PAYMENT_METHODS)


def _edit_prefill(c, deal_id: int) -> dict | None:
    """What the Edit deal screen opens on: the last thing typed for this deal
    (deal_entries), paired with the cards it created (deal_lines). A deal
    from before the entry log opens on what was booked, and says so."""
    det = get_deal(c, deal_id)
    if det is None:
        return None
    d = det["deal"]
    by_id = {(x["table"], x["id"]): x for x in v2cards.list_cards(c, include_disposed=True)}

    def out_line(t, i, typed=""):
        x = by_id.get((t, i)) or {}
        which = " · ".join(b for b in (x.get("set", ""),
                                        f"#{x['number']}" if x.get("number") else "",
                                        f"cert {x['cert']}" if x.get("cert") else f"id {i}") if b)
        return {"table": t, "id": i, "name": x.get("name", f"{t}#{i}"),
                "is_pc": x.get("is_pc", False), "which": which,
                "label": (f"{x.get('company', '')} {x.get('grade', '')}".strip()
                          if t == "graded_cards" else "RAW"),
                "mv": x.get("market_value"), "basis": x.get("basis"),
                "basis_unknown": x.get("basis_unknown", False),
                "deal_value": "" if typed in (None, "") else str(typed)}

    def s(v):
        if v in (None, ""):
            return ""
        if isinstance(v, float):
            return f"{v:.2f}".rstrip("0").rstrip(".")
        return str(v)

    row = c.execute("SELECT payload_json FROM deal_entries WHERE deal_id=? "
                    "ORDER BY id DESC LIMIT 1", (deal_id,)).fetchone()
    in_refs = [(r["card_table"], r["card_id"]) for r in c.execute(
        "SELECT card_table, card_id FROM deal_lines WHERE deal_id=? AND side='in' "
        "ORDER BY id", (deal_id,))]
    base = {"counterparty": d["counterparty"], "location": d["location"],
            "payment_method": d["payment_method"], "notes": d["notes"],
            "occurred_at": d["occurred_at"]}
    if row is not None:
        p = json.loads(row["payload_json"])
        if "cash_received" in p or "cash_paid" in p:
            got, paid = s(p.get("cash_received")), s(p.get("cash_paid"))
        else:
            cash = float(p.get("cash_amount") or 0)
            got, paid = (s(cash) if cash > 0 else ""), (s(-cash) if cash < 0 else "")
        outs = [out_line(x.get("table"), x.get("id"), x.get("deal_value"))
                for x in p.get("cards_out") or []]
        filled = [x for x in p.get("cards_in") or []
                  if any(s(x.get(k)).strip() for k in ("name", "deal_value",
                                                       "market_value", "cert"))]
        ins = []
        for k, x in enumerate(filled):
            ins.append({"ref": list(in_refs[k]) if k < len(in_refs) else x.get("ref"),
                        "name": x.get("name", ""), "set": x.get("set", ""),
                        "number": x.get("number", ""), "year": x.get("year", ""),
                        "company": x.get("company", "PSA"), "grade": x.get("grade", ""),
                        "cert": x.get("cert", ""), "is_graded": bool(x.get("is_graded", True)),
                        "deal_value": s(x.get("deal_value")),
                        "market_value": s(x.get("market_value"))})
        return {**base, "source": "entered", "cash_received": got, "cash_paid": paid,
                "ask": s(p.get("out_side_total")),
                "adjust": any(o["deal_value"] for o in outs),
                "cards_out": outs, "cards_in": ins}
    # no typing on record: open on what was booked, clearly labelled
    cash = d["cash_amount"] or 0
    # each outgoing card prefilled with what it booked, so saving unchanged
    # reproduces the deal exactly instead of re-splitting by today's markets
    outs = [out_line(x["_table"], x["id"], s(x.get("disposal_proceeds")))
            for x in det["cards_out"]]
    ins = [{"ref": [x["_table"], x["id"]], "name": x["card_name"],
            "set": x.get("set_name") or "", "number": x.get("card_number") or "",
            "year": x.get("year") or "",
            "company": x.get("grading_company") or "PSA", "grade": x.get("grade") or "",
            "cert": x.get("serial_number") or "", "is_graded": x["_table"] == "graded_cards",
            "deal_value": s(x.get("acquisition_price") if x["_table"] == "graded_cards"
                            else x.get("purchase_price")),
            "market_value": s(x.get("market_then"))}
           for x in det["cards_in"]]
    return {**base, "source": "booked", "cash_received": s(cash) if cash > 0 else "",
            "cash_paid": s(-cash) if cash < 0 else "", "ask": "",
            "adjust": bool(outs) and bool(cash or det["cards_in"]),
            "cards_out": outs, "cards_in": ins}


def _editable(det: dict) -> bool:
    """Edit deal replays the entry log, so a deal booked before it existed
    cannot be edited, only voided and rebooked."""
    return bool(det["editable"])


@app.get("/deals/<int:deal_id>/edit")
def deal_edit(deal_id):
    c = conn()
    det = get_deal(c, deal_id)
    if det is None:
        c.close()
        abort(404)
    if not _editable(det):
        c.close()
        # the deal page says why, where the Edit button would be
        return redirect(url_for("deal_detail", deal_id=deal_id))
    prefill = _edit_prefill(c, deal_id)
    cards = [x for x in v2cards.list_cards(c) if x["status"] == "active"]
    c.close()
    return render_template("deal_new.html", active="deals", cards=cards,
                           methods=v2db.PAYMENT_METHODS, edit_of=deal_id, prefill=prefill)


@app.get("/deals/<int:deal_id>")
def deal_detail(deal_id):
    c = conn()
    det = get_deal(c, deal_id)
    c.close()
    if det is None:
        abort(404)
    return render_template("deal_detail.html", active="deals", d=det["deal"],
                           cards_out=det["cards_out"], cards_in=det["cards_in"],
                           photos=det["photos"], entered=det["entered"],
                           revisions=det["revisions"], editable=_editable(det))


@app.get("/raw")
def raw_page():
    c = conn()
    rows = [x for x in v2cards.list_cards(c) if x["kind"] == "raw"]
    predictions = v2cards.prediction_stats(c)
    c.close()
    return render_template("raw.html", active="raw", cards=rows,
                           companies=v2db.GRADING_COMPANIES,
                           predictions=predictions)


@app.get("/psa-import")
def psa_import_page():
    return render_template("psa_import.html", active="raw")


@app.post("/api/psa/order-preview")
def api_psa_order_preview():
    text = request.get_json(force=True).get("csv", "")
    rows = psa_orders.read_order(text)
    if not rows:
        return jsonify({"ok": False, "error": "No cert rows found — is this a PSA order CSV?"}), 400
    c = conn()
    res = psa_orders.match_order(c, rows)
    c.close()
    graded = sum(1 for r in rows if r["grade"])
    return jsonify({"ok": True, "rows": rows, "kind": "completed" if graded else "open",
                    "graded_count": graded, **res})


@app.post("/api/psa/order-apply")
def api_psa_order_apply():
    p = request.get_json(force=True)
    rows = psa_orders.read_order(p.get("csv", ""))
    if not rows:
        return jsonify({"ok": False, "error": "No cert rows found — is this a PSA order CSV?"}), 400
    pairs = p.get("pairs")
    if (not isinstance(pairs, list) or not pairs
            or not all(isinstance(x, dict) and str(x.get("cert") or "").strip()
                       and isinstance(x.get("card_id"), int)
                       and not isinstance(x.get("card_id"), bool) for x in pairs)):
        return jsonify({"ok": False, "error": "pairs must be a non-empty list of "
                        "{cert, card_id} you ticked"}), 400
    fee = _parse_money(p.get("grading_cost_each"), "grading cost") or 0.0
    c = conn()
    res = psa_orders.apply_order(c, rows, pairs, order_id=str(p.get("order_id", "")),
                                 grading_cost_each=fee)
    c.close()
    return jsonify({"ok": True, **res})


@app.get("/deal-photos/<path:name>")
def deal_photo_file(name):
    return send_from_directory(v2db.DEAL_PHOTO_DIR, name)


@app.post("/api/quick-sale")
def api_quick_sale():
    """One-card cash sale without the full deal screen. Still books a real
    deal underneath, so cash pool, Show Day and reports all stay coherent."""
    p = request.get_json(force=True)
    try:
        table, card_id = _card_ref(p, "card_id")
        price = _parse_money(p.get("price"), "price")
    except (ValueError, TypeError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not price or price <= 0:
        return jsonify({"ok": False, "error": "price must be positive"}), 400
    counterparty = p.get("counterparty", "")
    method = p.get("payment_method", "cash")
    notes = p.get("notes", "Quick sale")
    c = conn()
    try:
        # the entry is in the deal screen's own shape, so replay() and Edit
        # deal read it like any other: one card out at the price, that cash in
        res = save_deal(
            c, cards_out=[CardOut(table, card_id, price)],
            cash_amount=price,
            counterparty=counterparty, payment_method=method, notes=notes,
            entry_payload={"source": "quick sale", "cash_amount": price,
                           "out_side_total": None,
                           "cards_out": [{"table": table, "id": card_id,
                                          "deal_value": price}],
                           "cards_in": [], "counterparty": counterparty,
                           "payment_method": method, "notes": notes})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "deal_id": res["deal_id"],
                    "realized_gain": res["out_lines"][0]["realized_gain"]})


@app.post("/api/deals/<int:deal_id>/void")
def api_void_deal(deal_id):
    """Undo a mistaken deal: restores cards that left, removes cards it
    created, deletes the deal. Refused if any created card has moved on.
    If a card it brought in was changed since (repriced, cert, renamed), the
    first call answers 409 with those changes; the screen shows them and
    resends with confirm_lose_changes once the user agrees to lose them."""
    from .deals import void_deal
    p = request.get_json(silent=True) or {}
    c = conn()
    try:
        res = void_deal(c, deal_id, **_confirm_flag(p))
    except LaterChangesError as e:
        return _later_changes(e)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **res})


def _confirm_flag(p) -> dict:
    """{"confirm_lose_changes": True} only when the request says exactly that.
    Left out otherwise, so the engine's own default (refuse) applies."""
    ok = isinstance(p, dict) and p.get("confirm_lose_changes") is True
    return {"confirm_lose_changes": True} if ok else {}


def _later_changes(e):
    return jsonify({"ok": False, "error": str(e),
                    "later_changes": list(getattr(e, "changes", []) or [])}), 409


@app.post("/api/crack")
def api_crack():
    """Crack a slab back to raw (e.g. to resubmit to a different grader)."""
    p = request.get_json(force=True)
    try:
        graded_id = int(p["graded_id"])
    except (ValueError, TypeError, KeyError):
        return jsonify({"ok": False, "error": "graded_id must be an integer"}), 400
    c = conn()
    try:
        raw_id = v2cards.crack_to_raw(
            c, graded_id,
            target_company=p.get("target_company", "PSA"),
            grading_status=p.get("grading_status", "Slated"))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "raw_id": raw_id})


@app.post("/api/cash/<int:entry_id>/delete")
def api_cash_delete(entry_id):
    c = conn()
    cur = c.execute("DELETE FROM cash_ledger WHERE id=?", (entry_id,))
    c.commit()
    if cur.rowcount == 0:
        return jsonify({"ok": False, "error": "entry not found"}), 404
    return jsonify({"ok": True, "balance": v2cards.cash_summary(c)["balance"]})


@app.post("/api/cash")
def api_cash():
    p = request.get_json(force=True)
    try:
        amount = float(str(p.get("amount", "")).replace("$", "").replace(",", ""))
    except ValueError:
        return jsonify({"ok": False, "error": "amount must be a number"}), 400
    if amount == 0:
        return jsonify({"ok": False, "error": "amount cannot be zero"}), 400
    c = conn()
    v2cards.add_cash_entry(c, amount, p.get("memo", ""))
    summary = v2cards.cash_summary(c)
    c.close()
    return jsonify({"ok": True, "balance": summary["balance"]})


# ── Phase 5: reports ────────────────────────────────────────────────────────────

def _realized_rows(c, year: str | None):
    rows = []
    for table in ("graded_cards", "ungraded_cards"):
        # 'lost' belongs here alongside 'disposed': the card left inventory and
        # booked a realized loss, and a P&L that hides losses is not a P&L
        for r in c.execute(
                f"SELECT * FROM {table} WHERE status IN ('disposed','lost') OR is_sold=1"
                if table == "graded_cards" else
                f"SELECT * FROM {table} WHERE status IN ('disposed','lost')"):
            disposed = (r["disposed_at"] or (r["sale_date"] if table == "graded_cards" else "") or "")
            if year and not disposed.startswith(year):
                continue
            basis = (r["acquisition_price"] if table == "graded_cards" else r["purchase_price"]) or 0
            proceeds = r["disposal_proceeds"]
            if proceeds is None and table == "graded_cards":
                proceeds = r["sale_price"]
            unknown = bool(r["basis_unknown"])
            gain = r["realized_gain"]
            # a flagged card has no knowable basis, so no knowable gain —
            # reporting proceeds as pure profit would inflate the totals
            if unknown:
                gain = None
            elif gain is None and proceeds is not None:
                gain = round(proceeds - basis, 2)
            rows.append({
                "name": r["card_name"], "kind": "slab" if table == "graded_cards" else "raw",
                "company": r["grading_company"] if table == "graded_cards" else "",
                "grade": r["grade"] if table == "graded_cards" else "",
                "set": r["set_name"] or "",
                "cert": (r["serial_number"] or "") if table == "graded_cards" else "",
                "acq_date": (r["acquisition_date"] if table == "graded_cards"
                             else r["purchase_date"]) or "",
                "basis": basis, "basis_unknown": unknown,
                "disposed": disposed[:10],
                "proceeds": proceeds, "gain": gain,
                "deal_id": r["disposed_via_deal_id"],
                "lost": r["status"] == "lost",
            })
    rows.sort(key=lambda x: x["disposed"], reverse=True)
    return rows


@app.get("/reports")
def reports_page():
    year = request.args.get("year") or ""
    c = conn()
    years = sorted({(r[0] or "")[:4] for r in c.execute(
        "SELECT disposed_at FROM graded_cards WHERE disposed_at IS NOT NULL "
        "UNION SELECT sale_date FROM graded_cards WHERE sale_date IS NOT NULL "
        "UNION SELECT disposed_at FROM ungraded_cards WHERE disposed_at IS NOT NULL")
        if r[0]}, reverse=True)
    rows = _realized_rows(c, year or None)
    c.close()
    # totals cover only cards with a known basis; flagged ones are counted
    # separately so the gain figure never includes invented profit
    known = [r for r in rows if not r["basis_unknown"]]
    unknown = [r for r in rows if r["basis_unknown"]]
    totals = {"basis": round(sum(r["basis"] or 0 for r in known), 2),
              "proceeds": round(sum(r["proceeds"] or 0 for r in known), 2),
              "gain": round(sum(r["gain"] or 0 for r in known), 2),
              "n_known": len(known), "n_unknown": len(unknown),
              "unknown_proceeds": round(sum(r["proceeds"] or 0 for r in unknown), 2)}
    # rows grouped by the month the card left, newest first — subtotals are
    # known-basis only, same rule as the totals above
    months = []
    for r in rows:
        key = r["disposed"][:7] or "undated"
        if not months or months[-1]["key"] != key:
            months.append({"key": key, "rows": [], "proceeds": 0.0, "gain": 0.0, "n_unknown": 0})
        m = months[-1]
        m["rows"].append(r)
        if r["basis_unknown"]:
            m["n_unknown"] += 1
        else:
            m["proceeds"] = round(m["proceeds"] + (r["proceeds"] or 0), 2)
            m["gain"] = round(m["gain"] + (r["gain"] or 0), 2)
    return render_template("reports.html", active="reports", rows=rows, months=months,
                           years=years, year=year, totals=totals)


@app.get("/reports/realized.csv")
def realized_csv():
    import csv
    import io
    year = request.args.get("year") or None
    c = conn()
    rows = _realized_rows(c, year)
    c.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Card", "Type", "Company", "Grade", "Set", "Cert", "Acquired",
                "Basis", "Moved", "Proceeds", "Gain", "Deal ID"])
    for r in rows:
        # "unknown" rather than 0/0 — a legacy import with no recorded price
        # must not read as a free card sold at 100% profit
        basis = "unknown" if r["basis_unknown"] else r["basis"]
        gain = "unknown" if r["basis_unknown"] else r["gain"]
        w.writerow([r["name"], r["kind"], r["company"], r["grade"], r["set"],
                    r["cert"], r["acq_date"], basis, r["disposed"],
                    r["proceeds"], gain, r["deal_id"] or ""])
    return app.response_class(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=realized_gains{('_' + year) if year else ''}.csv"})


@app.get("/reports/collection.csv")
def collection_csv():
    """Everything currently held — slabs + raw in one file."""
    import csv
    import io
    c = conn()
    cards = v2cards.list_cards(c)
    c.close()
    cards.sort(key=lambda x: ((x["name"] or "").lower()))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Card", "Type", "Company", "Grade", "Set", "Card #", "Year",
                "Cert", "Acquired", "Basis", "Market Value", "Gain", "Gain %",
                "Last Repriced", "Status"])
    for r in cards:
        unknown = r.get("basis_unknown")
        basis = "unknown" if unknown else r["basis"]
        gain = "unknown" if unknown else (r["gain"] if r["gain"] is not None else "")
        pct = ""
        if not unknown and r["gain"] is not None and r["basis"]:
            pct = f"{r['gain'] / r['basis'] * 100:.1f}"
        w.writerow([r["name"], r["kind"], r["company"], r["grade"], r["set"],
                    r["number"], r["year"], r["cert"], r["acq_date"], basis,
                    r["market_value"] if r["market_value"] is not None else "",
                    gain, pct, (r["repriced"] or "")[:10],
                    r.get("grading_status") or r["status"]])
    return app.response_class(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=collection_{date.today().isoformat()}.csv"})


def _sell_rows(c, pct: int):
    """Active slabs still profitable at pct% of market. Unknown-basis cards are
    excluded — no cost means no knowable profit. Personal-collection cards are
    excluded too: the dashboard count already left them out, but this list
    (the CSV and the printed sell sheet) did not, so the BGS 10 Mega Charizard
    on the owner's desk was showing up as stock for sale."""
    rows = []
    for r in c.execute("SELECT * FROM graded_cards WHERE status='active' "
                       "AND market_value IS NOT NULL AND basis_unknown=0 AND is_pc=0"):
        sale = r["market_value"] * pct / 100
        basis = r["acquisition_price"] or 0
        profit = sale - basis
        if profit > 0:
            rows.append({"name": r["card_name"], "company": r["grading_company"],
                         "grade": r["grade"], "number": r["card_number"] or "",
                         "set": r["set_name"] or "", "cert": r["serial_number"] or "",
                         "basis": basis, "market": r["market_value"],
                         "sale": round(sale, 2), "profit": round(profit, 2),
                         "margin": round(profit / sale * 100, 1) if sale else 0})
    rows.sort(key=lambda x: -x["profit"])
    return rows


@app.get("/reports/sell-list.csv")
def sell_list_csv():
    import csv
    import io
    pct = int(request.args.get("pct", 85))
    c = conn()
    rows = _sell_rows(c, pct)
    c.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Card", "Company", "Grade", "Set", "Card #", "Cert", "Basis",
                "Market Value", f"Sale @ {pct}%", "Profit", "Margin %"])
    for r in rows:
        w.writerow([r["name"], r["company"], r["grade"], r["set"], r["number"],
                    r["cert"], r["basis"], r["market"], r["sale"], r["profit"],
                    r["margin"]])
    w.writerow([])
    w.writerow([f"{len(rows)} cards profitable at {pct}%", "", "", "", "", "", "",
                "", round(sum(r["sale"] for r in rows), 2),
                round(sum(r["profit"] for r in rows), 2), ""])
    return app.response_class(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=sell_list_{pct}pct.csv"})


@app.get("/reports/sheets.zip")
def sheets_zip():
    """Google Sheets-compatible CSVs (PSA/BGS/CGC/TAG/SOLD/RAW) in one zip —
    drop into Drive for phone lookups when the laptop is off."""
    from . import sheets_export
    c = conn()
    data, counts = sheets_export.build_zip(c)
    c.close()
    return app.response_class(
        data, mimetype="application/zip",
        headers={"Content-Disposition":
                 f"attachment; filename={sheets_export.zip_filename()}"})


@app.get("/reports/sheet/<name>.csv")
def single_sheet(name):
    """Individual sheet download (PSA, BGS, CGC, TAG, SOLD, RAW)."""
    from . import sheets_export
    name = name.upper()
    c = conn()
    if name in sheets_export.COMPANIES:
        content, _ = sheets_export.graded_sheet(c, name)
    elif name == "SOLD":
        content, _ = sheets_export.sold_sheet(c)
    elif name == "RAW":
        content, _ = sheets_export.raw_sheet(c)
    else:
        c.close(); abort(404)
    c.close()
    return app.response_class(
        content, mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={name}.csv"})


@app.get("/reports/deals.csv")
def deals_csv():
    import csv
    import io
    c = conn()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Deal ID", "Date", "Counterparty", "Location", "Method",
                "Cash Amount", "Direction", "Line Type", "Card", "Company",
                "Grade", "Basis", "Proceeds", "Gain", "Notes"])
    for d in list_deals(c):
        det = get_deal(c, d["id"])
        base = [d["id"], d["occurred_at"], d["counterparty"], d["location"],
                d["payment_method"], d["cash_amount"]]
        if not det["cards_out"] and not det["cards_in"]:
            w.writerow(base + ["", "", "", "", "", "", "", "", d["notes"]])
        for x in det["cards_out"]:
            basis = (x["acquisition_price"] if x["_table"] == "graded_cards"
                     else x["purchase_price"]) or 0
            w.writerow(base + ["out", "slab" if x["_table"] == "graded_cards" else "raw",
                               x["card_name"], x.get("grading_company", ""),
                               x.get("grade", ""), basis, x["disposal_proceeds"],
                               x["realized_gain"], d["notes"]])
        for x in det["cards_in"]:
            basis = (x["acquisition_price"] if x["_table"] == "graded_cards"
                     else x["purchase_price"]) or 0
            w.writerow(base + ["in", "slab" if x["_table"] == "graded_cards" else "raw",
                               x["card_name"], x.get("grading_company", ""),
                               x.get("grade", ""), basis, "", "", d["notes"]])
    c.close()
    return app.response_class(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=deal_history.csv"})


@app.get("/evaluator")
def evaluator():
    """Scratchpad for sizing a trade/deal before committing — combines v1's
    Trade Evaluator and Deal Calculator. Writes nothing; hands off to /deals/new."""
    c = conn()
    cards = [x for x in v2cards.list_cards(c) if x["status"] == "active"]
    c.close()
    return render_template("evaluator.html", active="evaluator", cards=cards)


@app.get("/stock-check")
def stock_check():
    c = conn()
    cards = [x for x in v2cards.list_cards(c) if x["kind"] == "slab"
             and x["status"] == "active"]
    c.close()
    cards.sort(key=lambda x: ((x["company"] or ""), (x["name"] or "").lower()))
    companies = sorted({x["company"] for x in cards if x["company"]})
    return render_template("stock_check.html", active="stock-check",
                           cards=cards, companies=companies, total=len(cards))


@app.get("/stock-check/print")
def stock_check_print():
    company = request.args.get("company") or ""
    c = conn()
    cards = [x for x in v2cards.list_cards(c) if x["kind"] == "slab"
             and x["status"] == "active"
             and (not company or x["company"] == company)]
    c.close()
    cards.sort(key=lambda x: ((x["company"] or ""), (x["name"] or "").lower()))
    return render_template("stock_check_print.html", cards=cards, company=company)


@app.get("/inventory-sheet")
def inventory_sheet():
    """Printable full-inventory sheet — blank Sold Price column plus write-in
    rows, for working a show table with a pen."""
    kind = request.args.get("kind", "")       # "" | slab | raw
    c = conn()
    cards = [x for x in v2cards.list_cards(c) if not kind or x["kind"] == kind]
    c.close()
    cards.sort(key=lambda x: ((x["company"] or "zz"), (x["name"] or "").lower()))
    total_basis = sum(0 if x.get("basis_unknown") else (x["basis"] or 0) for x in cards)
    total_market = sum((x["market_value"] or 0) for x in cards)
    return render_template("inventory_sheet.html", cards=cards,
                           total_basis=round(total_basis, 2),
                           total_market=round(total_market, 2),
                           write_in_rows=18,
                           today=date.today().isoformat())


@app.get("/sell-sheet")
def sell_sheet():
    pct = int(request.args.get("pct", 85))
    c = conn()
    rows = _sell_rows(c, pct)
    c.close()
    return render_template("sell_sheet.html", rows=rows, pct=pct,
                           total_profit=round(sum(r["profit"] for r in rows), 2))


# ── JSON API ────────────────────────────────────────────────────────────────────

@app.post("/api/reprice")
def api_reprice():
    p = request.get_json(force=True)
    val = p.get("market_value")
    table = p.get("table", "graded_cards")
    if table not in ("graded_cards", "ungraded_cards"):
        return jsonify({"ok": False, "error": "bad table"}), 400
    basis_col = "acquisition_price" if table == "graded_cards" else "purchase_price"
    if "market_value" not in p:
        return jsonify({"ok": False, "error": "market_value is required (blank clears it)"}), 400
    try:
        _, card_id = _card_ref({**p, "table": table}, "card_id")
        mv = _parse_money(val, "market value")
    except (ValueError, TypeError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    c = conn()
    try:
        v2cards.reprice(c, card_id, mv, table=table)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    row = c.execute(f"SELECT market_value, market_value_updated, basis_unknown, "
                    f"{basis_col} AS basis FROM {table} WHERE id=?",
                    (card_id,)).fetchone()
    mv = row["market_value"]
    return jsonify({"ok": True, "market_value": mv,
                    "repriced": row["market_value_updated"],
                    "basis_unknown": bool(row["basis_unknown"]),
                    "gain": (round(mv - (row["basis"] or 0), 2)
                             if mv is not None and not row["basis_unknown"] else None)})


_EDITABLE = {
    "graded_cards": {"card_name", "set_name", "card_number", "year",
                     "grading_company", "grade", "serial_number", "notes",
                     "acquisition_price", "acquisition_date"},
    "ungraded_cards": {"card_name", "set_name", "card_number", "year", "notes",
                       "purchase_price", "purchase_date", "target_grading_company"},
}


@app.post("/api/card/update")
def api_card_update():
    p = request.get_json(force=True)
    table = p.get("table")
    if table not in _EDITABLE:
        return jsonify({"ok": False, "error": "bad table"}), 400
    fields = {k: v for k, v in (p.get("fields") or {}).items() if k in _EDITABLE[table]}
    if not fields:
        return jsonify({"ok": False, "error": "no editable fields supplied"}), 400
    try:
        card_id = int(p["id"])
    except (ValueError, TypeError, KeyError):
        return jsonify({"ok": False, "error": "id must be an integer"}), 400
    money_col = "acquisition_price" if table == "graded_cards" else "purchase_price"
    c = conn()
    cur = c.execute(f"SELECT * FROM {table} WHERE id=?", (card_id,)).fetchone()
    if cur is None:
        return jsonify({"ok": False, "error": "card not found"}), 404
    if money_col in fields:
        # A card that came in or left through a deal takes its numbers from
        # that deal; they change only through Edit deal, never from here.
        deal_ref = cur["acquired_via_deal_id"] or cur["disposed_via_deal_id"]
        if deal_ref:
            return jsonify({"ok": False, "error":
                            f"This card's cost comes from deal #{deal_ref}. "
                            "Change it with Edit deal on that deal's page."}), 400
        try:
            typed = _parse_money(fields.pop(money_col), "cost")
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        # blank clears what was typed; it never becomes $0
        fields["basis_entered"] = typed
    v2db.update_card(c, table, card_id, fields, "you", "drawer")
    c.commit()
    row = c.execute(f"SELECT * FROM {table} WHERE id=?", (card_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "card not found"}), 404
    basis = row[money_col] or 0
    mv = row["market_value"]          # both tables carry market_value now
    unknown = bool(row["basis_unknown"])
    return jsonify({"ok": True, "card": {
        "name": row["card_name"], "set": row["set_name"] or "",
        "number": row["card_number"] or "", "year": row["year"] or "",
        "company": row["grading_company"] if table == "graded_cards" else "",
        "grade": row["grade"] if table == "graded_cards" else "",
        "cert": row["serial_number"] if table == "graded_cards" else "",
        "acq_cost": basis, "basis": basis, "basis_unknown": unknown,
        "basis_source": v2cards._basis_source(row),
        "basis_deal": row["acquired_via_deal_id"] or row["disposed_via_deal_id"],
        "market_value": mv,
        "gain": (round(mv - basis, 2) if mv is not None and not unknown else None),
        "acq_date": (row["acquisition_date"] if table == "graded_cards"
                     else row["purchase_date"]) or "",
        "notes": row["notes"] or "",
    }})


@app.post("/api/psa/lookup")
def api_psa_lookup():
    """Look up a PSA cert on demand (e.g. from the New Deal 'cards in' row).
    Cache-first and budget-aware."""
    p = request.get_json(force=True)
    cert = str(p.get("cert", "")).strip()
    if not cert:
        return jsonify({"ok": False, "error": "Enter a cert number first."}), 400

    c = conn()
    token = config.get_keys()["psa_api_token"]
    res = psa_api.lookup_cert(c, cert, token)
    c.close()

    if res["status"] in ("cached", "fetched"):
        return jsonify({"ok": True, "status": res["status"], "data": res["data"]})

    messages = {
        "not_found": "No PSA record found for that cert number.",
        "no_token": "No PSA_API_TOKEN configured — add one in ~/.cardvaultmac/v2.env.",
        "rate_limited": res.get("error") or "PSA refused the call (429).",
        "queued": res.get("error") or "Today's PSA lookup budget is used up — resumes tomorrow.",
        "error": res.get("error") or "PSA lookup failed.",
    }
    return jsonify({"ok": False, "status": res["status"],
                    "error": messages.get(res["status"], res["status"])}), 200


def _parse_deal_payload(p):
    """The deal request, parsed strictly. Every money field is stored as typed
    or refused. An arriving line with nothing typed at all is the empty row the
    screen always offers and is skipped; a line with anything typed but no
    name is kept, so the engine refuses it instead of silently dropping it."""
    outs = []
    for x in p.get("cards_out") or []:
        table, cid = _card_ref({"table": x.get("table"), "id": x.get("id")})
        outs.append(CardOut(table, cid, _parse_money(x.get("deal_value"), "per-card value")))
    ins = []
    for x in p.get("cards_in") or []:
        name = str(x.get("name") or "").strip()
        dv = _parse_money(x.get("deal_value"), f"take-in for {name or 'an unnamed card'}")
        mv = _parse_money(x.get("market_value"), f"market for {name or 'an unnamed card'}")
        if not name and dv is None and mv is None and not str(x.get("cert") or "").strip():
            continue
        ins.append(CardIn(card_name=name, is_graded=bool(x.get("is_graded", True)),
                          set_name=x.get("set", ""), card_number=x.get("number", ""),
                          year=x.get("year", ""), grading_company=x.get("company", ""),
                          grade=x.get("grade", ""), serial_number=x.get("cert", ""),
                          deal_value=dv, market_value=mv))
    if "cash_received" in p or "cash_paid" in p:
        # the two boxes on the deal screen, each a plain non-negative amount
        got = _parse_money(p.get("cash_received"), "Cash I receive") or 0.0
        paid = _parse_money(p.get("cash_paid"), "Cash I pay") or 0.0
        cash_amount = round(got - paid, 2)
    else:
        cash_amount = _parse_money(p.get("cash_amount"), "cash", allow_negative=True) or 0.0
    out_total = _parse_money(p.get("out_side_total"), "ask")
    in_total = _parse_money(p.get("in_side_total"), "take-in total")
    return outs, ins, cash_amount, out_total, in_total


def _in_refs(p):
    """(table, id) of the card each arriving line already created, for Edit
    deal; None for a new line. Same filtering as _parse_deal_payload."""
    refs = []
    for x in p.get("cards_in") or []:
        if not (str(x.get("name") or "").strip() or str(x.get("deal_value") or "").strip()
                or str(x.get("market_value") or "").strip()
                or str(x.get("cert") or "").strip()):
            continue
        r = x.get("ref")
        ok = (isinstance(r, list) and len(r) == 2
              and r[0] in ("graded_cards", "ungraded_cards")
              and isinstance(r[1], int) and not isinstance(r[1], bool))
        refs.append((r[0], r[1]) if ok else None)
    return refs


@app.post("/api/deals/preview")
def api_deal_preview():
    """The full resolver, no write. The deal screen shows this on every edit,
    so the numbers on screen before Save are the engine's own."""
    p = request.get_json(force=True)
    try:
        outs, ins, cash_amount, out_total, in_total = _parse_deal_payload(p)
    except (ValueError, TypeError, KeyError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    c = conn()
    try:
        edit_of = p.get("edit_of")
        if isinstance(edit_of, int) and not isinstance(edit_of, bool):
            args = dict(cards_out=outs, cards_in=ins, in_card_refs=_in_refs(p),
                        cash_amount=cash_amount, out_side_total=out_total,
                        in_side_total=in_total, dry_run=True)
            try:
                res = edit_deal(c, edit_of, **args)
            except LaterChangesError as e:
                # a preview writes nothing, so show what would book and name
                # the later changes Save will ask about
                res = edit_deal(c, edit_of, confirm_lose_changes=True, **args)
                res = {**res, "later_changes": list(e.changes or [])}
        else:
            res = save_deal(c, cards_out=outs, cards_in=ins, cash_amount=cash_amount,
                            out_side_total=out_total, in_side_total=in_total,
                            dry_run=True)
    except ValueError as e:
        c.close()
        return jsonify({"ok": False, "error": str(e)}), 400
    c.close()
    return jsonify({"ok": True, **res})


@app.post("/api/deals")
def api_save_deal():
    p = request.get_json(force=True)
    try:
        outs, ins, cash_amount, out_total, in_total = _parse_deal_payload(p)
    except (ValueError, TypeError, KeyError) as e:
        return jsonify({"ok": False,
                        "error": str(e)}), 400
    c = conn()
    try:
        res = save_deal(
            c, cards_out=outs, cards_in=ins,
            cash_amount=cash_amount,
            occurred_at=p.get("occurred_at") or None,
            counterparty=p.get("counterparty", ""), location=p.get("location", ""),
            payment_method=p.get("payment_method", "cash"), notes=p.get("notes", ""),
            out_side_total=out_total, in_side_total=in_total,
            entry_payload=p)            # what was typed, verbatim, same transaction
    except ValueError as e:
        c.close()
        return jsonify({"ok": False, "error": str(e)}), 400
    c.close()
    return jsonify({"ok": True, **res})


@app.post("/api/deals/<int:deal_id>/edit")
def api_edit_deal(deal_id):
    """Save a new revision of a booked deal. The previous revision is archived
    in voided_deals and what was typed is appended to deal_entries."""
    p = request.get_json(force=True)
    try:
        outs, ins, cash_amount, out_total, in_total = _parse_deal_payload(p)
    except (ValueError, TypeError, KeyError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    c = conn()
    try:
        res = edit_deal(
            c, deal_id, cards_out=outs, cards_in=ins, in_card_refs=_in_refs(p),
            cash_amount=cash_amount, occurred_at=p.get("occurred_at") or None,
            counterparty=p.get("counterparty", ""), location=p.get("location", ""),
            payment_method=p.get("payment_method", "cash"), notes=p.get("notes", ""),
            out_side_total=out_total, in_side_total=in_total, entry_payload=p,
            **_confirm_flag(p))
    except LaterChangesError as e:
        c.close()
        return _later_changes(e)
    except ValueError as e:
        c.close()
        return jsonify({"ok": False, "error": str(e)}), 400
    c.close()
    return jsonify({"ok": True, **res})


@app.post("/api/deals/<int:deal_id>/photos")
def api_deal_photo(deal_id):
    f = request.files.get("photo")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "no file"}), 400
    c = conn()
    if c.execute("SELECT 1 FROM deals WHERE id=?", (deal_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": f"deal {deal_id} not found"}), 404
    v2db.DEAL_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(f.filename).suffix.lower() or ".jpg"
    name = f"deal{deal_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}"
    f.save(v2db.DEAL_PHOTO_DIR / name)
    add_deal_photo(c, deal_id, name, datetime.now().isoformat(timespec="seconds"))
    return jsonify({"ok": True, "file": name})


@app.post("/api/promote")
def api_promote():
    p = request.get_json(force=True)
    c = conn()
    try:
        gid = v2cards.promote_raw(
            c, int(p["ungraded_id"]),
            grading_company=p.get("company", "PSA"), grade=p.get("grade", ""),
            serial_number=p.get("cert", ""),
            grading_cost=float(p.get("grading_cost") or 0),
            return_date=p.get("return_date") or None)
    except (ValueError, KeyError) as e:
        c.close()
        return jsonify({"ok": False, "error": str(e)}), 400
    c.close()
    return jsonify({"ok": True, "graded_id": gid})


@app.post("/api/grading-status")
def api_grading_status():
    p = request.get_json(force=True)
    c = conn()
    v2cards.set_grading_status(c, int(p["ungraded_id"]), p["grading_status"])
    c.close()
    return jsonify({"ok": True})


@app.post("/api/expected-grade")
def api_expected_grade():
    p = request.get_json(force=True)
    c = conn()
    v2cards.set_expected_grade(c, int(p["ungraded_id"]), p.get("expected_grade", ""),
                               sub_type=p.get("sub_type"))
    c.close()
    return jsonify({"ok": True})


@app.post("/api/grading-bulk")
def api_grading_bulk():
    p = request.get_json(force=True)
    c = conn()
    res = v2cards.bulk_grading_update(
        c, p.get("ids", []),
        grading_status=p.get("grading_status"),
        expected_back=p.get("expected_back"),
        target_company=p.get("target_company"))
    c.close()
    return jsonify({"ok": True, **res})


@app.post("/api/expected-back")
def api_expected_back():
    p = request.get_json(force=True)
    c = conn()
    v2cards.set_expected_back(c, int(p["ungraded_id"]), p.get("expected_back", ""))
    c.close()
    return jsonify({"ok": True})


@app.post("/api/card/create")
def api_card_create():
    p = request.get_json(force=True)
    table = "graded_cards" if p.get("is_graded", True) else "ungraded_cards"
    basis = _parse_money(p.get("basis"), "cost basis")
    mv = _parse_money(p.get("market_value"), "market value")
    fee = _parse_money(p.get("grading_fee"), "grading fee")
    c = conn()
    new_id = v2cards.create_card(
        c, table,
        card_name=p.get("card_name", ""),
        basis=basis,
        market_value=mv,
        acquisition_date=p.get("acquisition_date", ""),
        grading_company=p.get("grading_company", "PSA"),
        grade=p.get("grade", ""),
        serial_number=p.get("serial_number", ""),
        card_number=p.get("card_number", ""),
        set_name=p.get("set_name", ""),
        year=p.get("year", ""),
        notes=p.get("notes", ""),
        grading_fee=fee,
        is_pc=bool(p.get("is_pc")))
    c.close()
    return jsonify({"ok": True, "id": new_id, "table": table})


@app.post("/api/confirm-price")
def api_confirm_price():
    p = request.get_json(force=True)
    c = conn()
    table, cid = _card_ref({**p, "table": p.get("table", "graded_cards")}, "card_id")
    res = v2cards.confirm_price(c, cid, table)
    c.close()
    return jsonify({"ok": True, **res})


@app.post("/api/card/remove")
def api_card_remove():
    p = request.get_json(force=True)
    c = conn()
    table, cid = _card_ref(p)
    res = v2cards.remove_card(c, table, cid, str(p.get("reason", ""))[:500])
    c.close()
    return jsonify({"ok": True, **res})


@app.post("/api/card/lost")
def api_card_lost():
    p = request.get_json(force=True)
    c = conn()
    try:
        table, cid = _card_ref(p)
        if p.get("reason") not in v2db.LOST_REASONS:
            raise ValueError(f"reason must be one of {v2db.LOST_REASONS}")
        res = v2cards.mark_lost(c, table, cid, p["reason"], str(p.get("note", ""))[:500])
    except ValueError as e:
        c.close()
        return jsonify({"ok": False, "error": str(e)}), 400
    c.close()
    return jsonify({"ok": True, **res})


@app.post("/api/card/restore")
def api_card_restore():
    p = request.get_json(force=True)
    c = conn()
    try:
        res = v2cards.restore_card(c, *_card_ref(p))
    except ValueError as e:
        c.close()
        return jsonify({"ok": False, "error": str(e)}), 400
    c.close()
    return jsonify({"ok": True, **res})


@app.get("/api/card/history")
def api_card_history():
    """What the collection drawer shows under History: the deal a card came in
    on, its repricings, and the lifecycle trail kept in its notes."""
    table = request.args.get("table", "graded_cards")
    if table not in ("graded_cards", "ungraded_cards"):
        return jsonify({"ok": False, "error": "unknown table"}), 400
    try:
        card_id = int(request.args.get("id", ""))
    except ValueError:
        return jsonify({"ok": False, "error": "bad id"}), 400
    c = conn()
    row = c.execute(f"SELECT * FROM {table} WHERE id=?", (card_id,)).fetchone()
    if row is None:
        c.close()
        return jsonify({"ok": False, "error": "card not found"}), 404
    deal = None
    if row["acquired_via_deal_id"]:
        d = c.execute("SELECT id, occurred_at, counterparty, cash_amount FROM deals WHERE id=?",
                      (row["acquired_via_deal_id"],)).fetchone()
        if d:
            deal = {"id": d["id"], "date": d["occurred_at"][:10], "counterparty": d["counterparty"] or "",
                    "cash": d["cash_amount"]}
    prices = []
    if table == "graded_cards":
        prices = [{"date": r["recorded_at"][:10], "value": r["market_value"],
                   "source": r["source"]} for r in c.execute(
            "SELECT recorded_at, market_value, source FROM price_history WHERE card_id=? "
            "ORDER BY recorded_at, id", (card_id,))]
    events = [dict(r) for r in c.execute(
        "SELECT at, field, old_value, new_value, source, ref FROM card_events "
        "WHERE card_table=? AND card_id=? ORDER BY id DESC", (table, card_id))]
    c.close()
    return jsonify({"ok": True, "deal": deal, "prices": prices, "events": events,
                    "notes": row["notes"] or "", "status": row["status"],
                    "disposed_at": row["disposed_at"], "proceeds": row["disposal_proceeds"],
                    "realized_gain": row["realized_gain"]})


@app.post("/api/pc")
def api_pc():
    p = request.get_json(force=True)
    c = conn()
    table, cid = _card_ref(p)
    if not isinstance(p.get("is_pc"), bool):
        raise ValueError("is_pc must be true or false")
    v2cards.set_pc(c, table, cid, p["is_pc"])
    c.close()
    return jsonify({"ok": True})


def _integrity_report():
    """Run every data-integrity invariant against the live database. The
    result is shown as a banner on every page until it is clean, so a broken
    number can never sit unnoticed until it bites at tax time."""
    from . import checks as v2checks
    c = v2db.get_connection()       # runs before the app serves, so no request context
    found = []
    try:
        for fn in v2checks.CHECKS:
            try:
                bad = fn(c)
            except Exception as e:                       # a crashed check is itself a finding
                bad = [f"check crashed: {type(e).__name__}: {e}"]
            if bad:
                found.append({"check": fn.__name__, "n": len(bad), "first": bad[:3]})
    finally:
        c.close()
    return found


@app.context_processor
def _inject_integrity():
    return {"integrity": app.config.get("INTEGRITY", [])}


def main():
    guard = v2db.get_connection()   # startup guard: refuse to boot against v1
    guard.close()
    v2db.backup_v2()                # daily snapshot BEFORE any migration touches the db
    app.config["LAST_BACKUP"] = date.today()   # arms the new-day backup in _daily_backup
    guard = v2db.get_connection()
    v2db.migrate_schema(guard)      # idempotent — applies any new v2 tables
    guard.close()
    app.config["INTEGRITY"] = _integrity_report()
    if app.config["INTEGRITY"]:
        print(f"  DATA INTEGRITY: {len(app.config['INTEGRITY'])} check(s) failing — see the banner in the app")
    config.ensure_env_file()        # create ~/.cardvaultmac/v2.env template
    host = config.get_host()
    print(f"CardVault v2 — http://{'127.0.0.1' if host == '127.0.0.1' else host}:5177  (Ctrl+C to stop)")
    if host != "127.0.0.1":
        print("  bound to all interfaces — reachable from your tailnet/LAN devices")
    app.run(host=host, port=5177, debug=False)


if __name__ == "__main__":
    main()
