"""
CardVault v2 — local web app.

Run:  python3 -m v2.app          (serves http://127.0.0.1:5177)

The v2 startup guard runs before the server binds: if the v2 database is
missing or unmarked, the app refuses to start. v1 is never opened for writing.
"""

import json
from datetime import date, datetime
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template,
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
    return v2db.get_connection()


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
                           snaps_json=json.dumps(snaps), recent=recent)


@app.get("/collection")
def collection():
    c = conn()
    cards = v2cards.list_cards(c)
    sets = sorted({x["set"] for x in cards if x["set"]}, key=str.lower)
    c.close()
    return render_template("collection.html", active="collection",
                           cards_json=json.dumps(cards), sets=sets)


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
    cards = [x for x in v2cards.list_cards(c) if x["status"] == "active"
             or x["status"] == "submitted_for_grading"]
    c.close()
    return render_template("deal_new.html", active="deals",
                           cards_json=json.dumps(cards),
                           methods=v2db.PAYMENT_METHODS)


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
    c.close()
    return render_template("raw.html", active="raw", cards=rows,
                           companies=v2db.GRADING_COMPANIES)


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
    ids = []
    for f in files:
        if not f.filename:
            continue
        pid = v2photos.save_upload(c, f)
        if card_id:                      # backfill mode: pre-match to a card
            c.execute("UPDATE photo_imports SET matched_table='graded_cards', "
                      "matched_id=? WHERE id=?", (int(card_id), pid))
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
            gain = r["realized_gain"]
            if gain is None and proceeds is not None:
                gain = round(proceeds - basis, 2)
            rows.append({
                "name": r["card_name"], "kind": "slab" if table == "graded_cards" else "raw",
                "company": r["grading_company"] if table == "graded_cards" else "",
                "grade": r["grade"] if table == "graded_cards" else "",
                "set": r["set_name"] or "",
                "cert": (r["serial_number"] or "") if table == "graded_cards" else "",
                "acq_date": (r["acquisition_date"] if table == "graded_cards"
                             else r["purchase_date"]) or "",
                "basis": basis, "disposed": disposed[:10],
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
    totals = {"basis": round(sum(r["basis"] or 0 for r in rows), 2),
              "proceeds": round(sum(r["proceeds"] or 0 for r in rows), 2),
              "gain": round(sum(r["gain"] or 0 for r in rows), 2)}
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
        w.writerow([r["name"], r["kind"], r["company"], r["grade"], r["set"],
                    r["cert"], r["acq_date"], r["basis"], r["disposed"],
                    r["proceeds"], r["gain"], r["deal_id"] or ""])
    return app.response_class(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=realized_gains{('_' + year) if year else ''}.csv"})


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


@app.get("/sell-sheet")
def sell_sheet():
    pct = int(request.args.get("pct", 85))
    c = conn()
    rows = []
    for r in c.execute("SELECT * FROM graded_cards WHERE status='active' "
                       "AND market_value IS NOT NULL"):
        sale = r["market_value"] * pct / 100
        profit = sale - (r["acquisition_price"] or 0)
        if profit > 0:
            rows.append({"name": r["card_name"], "company": r["grading_company"],
                         "grade": r["grade"], "number": r["card_number"] or "",
                         "basis": r["acquisition_price"] or 0,
                         "market": r["market_value"], "sale": round(sale, 2),
                         "profit": round(profit, 2)})
    c.close()
    rows.sort(key=lambda x: -x["profit"])
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
    c = conn()
    v2cards.reprice(c, int(p["card_id"]), float(val) if val not in (None, "") else None)
    row = c.execute("SELECT market_value, market_value_updated, acquisition_price "
                    "FROM graded_cards WHERE id=?", (p["card_id"],)).fetchone()
    c.close()
    mv = row["market_value"]
    return jsonify({"ok": True, "market_value": mv,
                    "repriced": row["market_value_updated"],
                    "gain": (round(mv - (row["acquisition_price"] or 0), 2)
                             if mv is not None else None)})


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
    for money_col in ("acquisition_price", "purchase_price"):
        if money_col in fields:
            try:
                fields[money_col] = float(str(fields[money_col]).replace("$", "").replace(",", "") or 0)
            except ValueError:
                return jsonify({"ok": False, "error": f"{money_col} must be a number"}), 400
    c = conn()
    sets = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*fields.values(), int(p["id"])])
    c.commit()
    row = c.execute(f"SELECT * FROM {table} WHERE id=?", (int(p["id"]),)).fetchone()
    c.close()
    if row is None:
        return jsonify({"ok": False, "error": "card not found"}), 404
    basis = (row["acquisition_price"] if table == "graded_cards" else row["purchase_price"]) or 0
    mv = row["market_value"] if table == "graded_cards" else None
    return jsonify({"ok": True, "card": {
        "name": row["card_name"], "set": row["set_name"] or "",
        "number": row["card_number"] or "", "year": row["year"] or "",
        "company": row["grading_company"] if table == "graded_cards" else "",
        "grade": row["grade"] if table == "graded_cards" else "",
        "cert": row["serial_number"] if table == "graded_cards" else "",
        "acq_cost": basis, "basis": basis,
        "gain": (round(mv - basis, 2) if mv is not None else None),
        "acq_date": (row["acquisition_date"] if table == "graded_cards"
                     else row["purchase_date"]) or "",
        "notes": row["notes"] or "",
    }})


@app.post("/api/deals")
def api_save_deal():
    p = request.get_json(force=True)
    outs = [CardOut(x["table"], int(x["id"]),
                    float(x["deal_value"]) if x.get("deal_value") not in (None, "") else None)
            for x in p.get("cards_out", [])]
    ins = [CardIn(card_name=x.get("name", "").strip(),
                  is_graded=bool(x.get("is_graded", True)),
                  set_name=x.get("set", ""), card_number=x.get("number", ""),
                  year=x.get("year", ""), grading_company=x.get("company", ""),
                  grade=x.get("grade", ""), serial_number=x.get("cert", ""),
                  deal_value=(float(x["deal_value"])
                              if x.get("deal_value") not in (None, "") else None))
           for x in p.get("cards_in", []) if x.get("name", "").strip()]
    c = conn()
    try:
        res = save_deal(
            c, cards_out=outs, cards_in=ins,
            cash_amount=float(p.get("cash_amount") or 0),
            occurred_at=p.get("occurred_at") or None,
            counterparty=p.get("counterparty", ""), location=p.get("location", ""),
            payment_method=p.get("payment_method", "cash"), notes=p.get("notes", ""),
            out_side_total=(float(p["out_side_total"])
                            if p.get("out_side_total") not in (None, "") else None),
            in_side_total=(float(p["in_side_total"])
                           if p.get("in_side_total") not in (None, "") else None))
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
    v2db.DEAL_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(f.filename).suffix.lower() or ".jpg"
    name = f"deal{deal_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}"
    f.save(v2db.DEAL_PHOTO_DIR / name)
    c = conn()
    add_deal_photo(c, deal_id, name, datetime.now().isoformat(timespec="seconds"))
    c.close()
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


def main():
    guard = v2db.get_connection()   # startup guard: refuse to boot against v1
    v2db.migrate_schema(guard)      # idempotent — applies any new v2 tables
    guard.close()
    config.ensure_env_file()        # create ~/.cardvaultmac/v2.env template
    host = config.get_host()
    print(f"CardVault v2 — http://{'127.0.0.1' if host == '127.0.0.1' else host}:5177  (Ctrl+C to stop)")
    if host != "127.0.0.1":
        print("  bound to all interfaces — reachable from your tailnet/LAN devices")
    app.run(host=host, port=5177, debug=False)


if __name__ == "__main__":
    main()
