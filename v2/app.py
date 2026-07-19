"""
CardVault v2 — local web app.

Run:  python3 -m v2.app          (serves http://127.0.0.1:5177)

The v2 startup guard runs before the server binds: if the v2 database is
missing or unmarked, the app refuses to start. v1 is never opened for writing.
"""

import json
from datetime import date, datetime
from pathlib import Path

from flask import (Flask, abort, g, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)

from . import cards as v2cards
from . import config
from . import db as v2db
from . import photos as v2photos
from . import psa_api
from .deals import CardIn, CardOut, add_deal_photo, get_deal, list_deals, save_deal

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024   # 32 MB uploads


@app.template_filter("money")
def _money(v):
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


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


@app.errorhandler(Exception)
def _api_errors(e):
    """API callers get JSON errors, never an HTML 500 page. Non-API routes and
    deliberate HTTP errors (404s etc.) pass through untouched."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    if request.path.startswith("/api/"):
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    raise e


# ── pages ───────────────────────────────────────────────────────────────────────

@app.get("/")
def dashboard():
    c = conn()
    v2cards.record_snapshot(c)
    stats = v2cards.dashboard_stats(c)
    extras = v2cards.dashboard_extras(c)
    snaps = v2cards.snapshots(c)
    recent = []
    for d in list_deals(c)[:8]:
        det = get_deal(c, d["id"])
        recent.append({
            "id": d["id"], "occurred_at": d["occurred_at"],
            "counterparty": d["counterparty"], "cash": d["cash_amount"],
            "n_out": len(det["cards_out"]), "n_in": len(det["cards_in"]),
            "gain": round(sum((x["realized_gain"] or 0) for x in det["cards_out"]), 2),
        })
    tstats = v2cards.trade_stats(c)
    cash = v2cards.cash_summary(c)
    movers = v2cards.top_movers(c)
    grading = v2cards.at_grading(c)
    sell = v2cards.sell_candidates(c)
    c.close()
    return render_template("dashboard.html", active="dashboard",
                           stats=stats, extras=extras, tstats=tstats,
                           cash=cash, movers=movers, grading=grading, sell=sell,
                           snaps=snaps, recent=recent)


@app.get("/collection")
def collection():
    c = conn()
    cards = v2cards.list_cards(c)
    sets = sorted({x["set"] for x in cards if x["set"]}, key=str.lower)
    c.close()
    return render_template("collection.html", active="collection",
                           cards=cards, sets=sets)


@app.get("/deals")
def deals_page():
    c = conn()
    today = date.today().isoformat()
    rows = []
    cash_in = cash_out = day_gain = 0.0
    today_rows = []
    for d in list_deals(c):
        det = get_deal(c, d["id"])
        gain = round(sum((x["realized_gain"] or 0) for x in det["cards_out"]), 2)
        row = {"id": d["id"], "occurred_at": d["occurred_at"],
               "counterparty": d["counterparty"], "location": d["location"],
               "method": d["payment_method"], "cash": d["cash_amount"],
               "n_out": len(det["cards_out"]), "n_in": len(det["cards_in"]),
               "gain": gain}
        rows.append(row)
        if d["occurred_at"][:10] == today:
            today_rows.append(row)
            if d["cash_amount"] > 0:
                cash_in += d["cash_amount"]
            else:
                cash_out += -d["cash_amount"]
            day_gain += gain
    c.close()
    return render_template("deals.html", active="deals", deals=rows,
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


@app.get("/deals/<int:deal_id>")
def deal_detail(deal_id):
    c = conn()
    det = get_deal(c, deal_id)
    c.close()
    if det is None:
        abort(404)
    return render_template("deal_detail.html", active="deals", d=det["deal"],
                           cards_out=det["cards_out"], cards_in=det["cards_in"],
                           photos=det["photos"])


@app.get("/raw")
def raw_page():
    c = conn()
    rows = [x for x in v2cards.list_cards(c) if x["kind"] == "raw"]
    predictions = v2cards.prediction_stats(c)
    c.close()
    return render_template("raw.html", active="raw", cards=rows,
                           companies=v2db.GRADING_COMPANIES,
                           predictions=predictions)


@app.get("/deal-photos/<path:name>")
def deal_photo_file(name):
    return send_from_directory(v2db.DEAL_PHOTO_DIR, name)


@app.get("/slab-photos/<path:name>")
def slab_photo_file(name):
    return send_from_directory(v2db.SLAB_PHOTO_DIR, name)


# ── Phase 4: slab photos + backfill ────────────────────────────────────────────

@app.get("/photos")
def photos_page():
    c = conn()
    imports = [dict(r) for r in v2photos.list_imports(c)]
    for i in imports:
        if i["extracted_json"]:
            i["ext"] = json.loads(i["extracted_json"])
    total_cost = sum(i["extract_cost"] or 0 for i in imports)
    budget_left = psa_api.budget_remaining(c)
    keys = config.get_keys()
    c.close()
    return render_template("photos.html", active="photos", imports=imports,
                           total_cost=total_cost, budget_left=budget_left,
                           have_anthropic=bool(keys["anthropic_api_key"]),
                           have_psa=bool(keys["psa_api_token"]))


@app.get("/photos/<int:import_id>")
def photo_review(import_id):
    c = conn()
    row = c.execute("SELECT * FROM photo_imports WHERE id=?", (import_id,)).fetchone()
    if row is None:
        c.close(); abort(404)
    ext = json.loads(row["extracted_json"]) if row["extracted_json"] else {"fields": {}, "low_confidence": []}
    cert = json.loads(row["cert_verified_json"]) if row["cert_verified_json"] else None

    current, match = None, {"exact": None, "candidates": []}
    if row["matched_id"]:
        cur_row = c.execute("SELECT * FROM graded_cards WHERE id=?", (row["matched_id"],)).fetchone()
        if cur_row:
            current = dict(cur_row)
    else:
        match = v2photos.find_match(c, ext["fields"], cert)
        if match["exact"]:
            current = match["exact"]
    c.close()

    # build per-field review rows: extracted | cert | current | proposed
    field_map = [("grading_company", "Company"), ("grade", "Grade"),
                 ("cert_number", "Cert #"), ("card_name", "Card name"),
                 ("set_name", "Set"), ("card_number", "Card #"), ("year", "Year")]
    card_col = {"cert_number": "serial_number"}
    rows = []
    for key, label in field_map:
        col = card_col.get(key, key)
        ext_v = ext["fields"].get(key, "")
        cert_v = (cert or {}).get(key, "")
        cur_v = str(current.get(col, "") or "") if current else ""
        proposed = cert_v or ext_v or cur_v          # cert-verified wins on disagreement
        rows.append({
            "key": col, "label": label,
            "extracted": ext_v, "cert": cert_v, "current": cur_v,
            "proposed": proposed,
            "low_conf": key in ext.get("low_confidence", []) and not cert_v,
            "changed": proposed != cur_v and proposed != "",
        })
    return render_template("photo_review.html", active="photos",
                           imp=row, rows=rows, current=current,
                           candidates=match["candidates"], cert=cert)


@app.post("/api/photos/upload")
def api_photos_upload():
    files = request.files.getlist("photos")
    if not files:
        return jsonify({"ok": False, "error": "no files"}), 400
    card_id = request.form.get("card_id")
    c = conn()
    if card_id:
        try:
            card_id = int(card_id)
        except ValueError:
            return jsonify({"ok": False, "error": "card_id must be an integer"}), 400
        if c.execute("SELECT 1 FROM graded_cards WHERE id=?", (card_id,)).fetchone() is None:
            return jsonify({"ok": False, "error": f"card {card_id} not found"}), 404
    ids = []
    for f in files:
        if not f.filename:
            continue
        pid = v2photos.save_upload(c, f)
        if card_id:                      # backfill mode: pre-match to a card
            c.execute("UPDATE photo_imports SET matched_table='graded_cards', "
                      "matched_id=? WHERE id=?", (card_id, pid))
            c.commit()
        ids.append(pid)
    c.close()
    return jsonify({"ok": True, "ids": ids})


@app.post("/api/photos/<int:import_id>/extract")
def api_photo_extract(import_id):
    c = conn()
    res = v2photos.run_extract(c, import_id)
    c.close()
    status = 200 if res.get("ok") else 500
    return jsonify(res), status


@app.post("/api/photos/extract-all")
def api_photo_extract_all():
    c = conn()
    pending = [r["id"] for r in v2photos.list_imports(c, "pending")]
    done, errors, cost = 0, [], 0.0
    for pid in pending:
        res = v2photos.run_extract(c, pid)
        if res.get("ok"):
            done += 1
            cost += res.get("cost", 0)
        else:
            errors.append({"id": pid, "error": res.get("error")})
    c.close()
    return jsonify({"ok": True, "processed": done, "cost": round(cost, 4),
                    "errors": errors})


@app.post("/api/photos/<int:import_id>/apply")
def api_photo_apply(import_id):
    p = request.get_json(force=True)
    c = conn()
    try:
        card_id = v2photos.apply_to_card(
            c, import_id,
            int(p["card_id"]) if p.get("card_id") else None,
            p.get("fields") or {})
    except (ValueError, KeyError) as e:
        c.close()
        return jsonify({"ok": False, "error": str(e)}), 400
    c.close()
    return jsonify({"ok": True, "card_id": card_id})


@app.post("/api/photos/<int:import_id>/reject")
def api_photo_reject(import_id):
    c = conn()
    v2photos.reject(c, import_id)
    c.close()
    return jsonify({"ok": True})


@app.post("/api/quick-sale")
def api_quick_sale():
    """One-card cash sale without the full deal screen. Still books a real
    deal underneath, so cash pool, Show Day and reports all stay coherent."""
    p = request.get_json(force=True)
    try:
        table = p["table"]
        card_id = int(p["card_id"])
        price = float(str(p["price"]).replace("$", "").replace(",", ""))
    except (ValueError, TypeError, KeyError):
        return jsonify({"ok": False, "error": "table, card_id and a numeric price are required"}), 400
    if table not in ("graded_cards", "ungraded_cards"):
        return jsonify({"ok": False, "error": "bad table"}), 400
    if price <= 0:
        return jsonify({"ok": False, "error": "price must be positive"}), 400
    c = conn()
    try:
        res = save_deal(
            c, cards_out=[CardOut(table, card_id, price)],
            cash_amount=price,
            counterparty=p.get("counterparty", ""),
            payment_method=p.get("payment_method", "cash"),
            notes=p.get("notes", "Quick sale"))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "deal_id": res["deal_id"],
                    "realized_gain": res["out_lines"][0]["realized_gain"]})


@app.post("/api/deals/<int:deal_id>/void")
def api_void_deal(deal_id):
    """Undo a mistaken deal: restores cards that left, removes cards it
    created, deletes the deal. Refused if any created card has moved on."""
    from .deals import void_deal
    c = conn()
    try:
        res = void_deal(c, deal_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **res})


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
        for r in c.execute(f"SELECT * FROM {table} WHERE status='disposed' OR is_sold=1"
                           if table == "graded_cards" else
                           f"SELECT * FROM {table} WHERE status='disposed'"):
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
    return render_template("reports.html", active="reports", rows=rows,
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
    excluded — no cost means no knowable profit."""
    rows = []
    for r in c.execute("SELECT * FROM graded_cards WHERE status='active' "
                       "AND market_value IS NOT NULL AND basis_unknown=0"):
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


@app.get("/backfill")
def backfill_page():
    c = conn()
    items = v2photos.incomplete_cards(c)
    total_active = c.execute(
        "SELECT COUNT(*) FROM graded_cards WHERE status='active'").fetchone()[0]
    c.close()
    return render_template("backfill.html", active="backfill", items=items,
                           total_active=total_active)


# ── JSON API ────────────────────────────────────────────────────────────────────

@app.post("/api/reprice")
def api_reprice():
    p = request.get_json(force=True)
    val = p.get("market_value")
    table = p.get("table", "graded_cards")
    if table not in ("graded_cards", "ungraded_cards"):
        return jsonify({"ok": False, "error": "bad table"}), 400
    basis_col = "acquisition_price" if table == "graded_cards" else "purchase_price"
    try:
        card_id = int(p["card_id"])
        mv = float(val) if val not in (None, "") else None
    except (ValueError, TypeError, KeyError):
        return jsonify({"ok": False, "error": "card_id must be an integer and "
                        "market_value a number"}), 400
    c = conn()
    try:
        v2cards.reprice(c, card_id, mv, table=table)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
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
    for money_col in ("acquisition_price", "purchase_price"):
        if money_col in fields:
            try:
                fields[money_col] = float(str(fields[money_col]).replace("$", "").replace(",", "") or 0)
            except ValueError:
                return jsonify({"ok": False, "error": f"{money_col} must be a number"}), 400
    money_col = "acquisition_price" if table == "graded_cards" else "purchase_price"
    c = conn()
    sets = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*fields.values(), card_id])
    # supplying a real cost resolves "basis unknown" — and a disposed card's
    # stored realized_gain must follow the corrected basis
    if fields.get(money_col):
        c.execute(f"UPDATE {table} SET basis_unknown=0 WHERE id=?", (card_id,))
        c.execute(f"""UPDATE {table} SET realized_gain =
                        ROUND(COALESCE(disposal_proceeds,{('sale_price' if table=='graded_cards' else 'NULL')})
                              - {money_col}, 2)
                      WHERE id=? AND status='disposed'""", (card_id,))
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
        "market_value": mv,
        "gain": (round(mv - basis, 2) if mv is not None and not unknown else None),
        "acq_date": (row["acquisition_date"] if table == "graded_cards"
                     else row["purchase_date"]) or "",
        "notes": row["notes"] or "",
    }})


@app.post("/api/psa/lookup")
def api_psa_lookup():
    """Look up a PSA cert on demand (e.g. from the New Deal 'cards in' row).
    Cache-first and budget-aware — reuses the same client as Slab Photos."""
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


@app.post("/api/deals")
def api_save_deal():
    p = request.get_json(force=True)
    try:
        outs = [CardOut(x["table"], int(x["id"]),
                        float(x["deal_value"]) if x.get("deal_value") not in (None, "") else None)
                for x in p.get("cards_out", [])]
        ins = [CardIn(card_name=x.get("name", "").strip(),
                      is_graded=bool(x.get("is_graded", True)),
                      set_name=x.get("set", ""), card_number=x.get("number", ""),
                      year=x.get("year", ""), grading_company=x.get("company", ""),
                      grade=x.get("grade", ""), serial_number=x.get("cert", ""),
                      deal_value=(float(x["deal_value"])
                                  if x.get("deal_value") not in (None, "") else None),
                      market_value=(float(x["market_value"])
                                    if x.get("market_value") not in (None, "") else None))
               for x in p.get("cards_in", []) if x.get("name", "").strip()]
        cash_amount = float(p.get("cash_amount") or 0)
        out_total = (float(p["out_side_total"])
                     if p.get("out_side_total") not in (None, "") else None)
        in_total = (float(p["in_side_total"])
                    if p.get("in_side_total") not in (None, "") else None)
    except (ValueError, TypeError, KeyError) as e:
        return jsonify({"ok": False,
                        "error": f"invalid deal payload: {e}"}), 400
    c = conn()
    try:
        res = save_deal(
            c, cards_out=outs, cards_in=ins,
            cash_amount=cash_amount,
            occurred_at=p.get("occurred_at") or None,
            counterparty=p.get("counterparty", ""), location=p.get("location", ""),
            payment_method=p.get("payment_method", "cash"), notes=p.get("notes", ""),
            out_side_total=out_total, in_side_total=in_total)
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
    v2cards.set_expected_grade(c, int(p["ungraded_id"]), p.get("expected_grade", ""))
    c.close()
    return jsonify({"ok": True})


def main():
    guard = v2db.get_connection()   # startup guard: refuse to boot against v1
    v2db.migrate_schema(guard)      # idempotent — applies any new v2 tables
    guard.close()
    v2db.backup_v2()                # daily local + iCloud snapshot of the v2 db
    config.ensure_env_file()        # create ~/.cardvaultmac/v2.env template
    host = config.get_host()
    print(f"CardVault v2 — http://{'127.0.0.1' if host == '127.0.0.1' else host}:5177  (Ctrl+C to stop)")
    if host != "127.0.0.1":
        print("  bound to all interfaces — reachable from your tailnet/LAN devices")
    app.run(host=host, port=5177, debug=False)


if __name__ == "__main__":
    main()
