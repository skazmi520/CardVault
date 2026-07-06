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
from . import db as v2db
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
    c.close()
    return render_template("dashboard.html", active="dashboard",
                           stats=stats, snaps_json=json.dumps(snaps), recent=recent)


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
    v2db.get_connection().close()   # startup guard: refuse to boot against v1
    print("CardVault v2 — http://127.0.0.1:5177  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=5177, debug=False)


if __name__ == "__main__":
    main()
