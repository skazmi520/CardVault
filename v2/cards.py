"""CardVault v2 — card operations: listing, repricing, promotion, dashboard stats."""

from datetime import date, datetime

from . import db as v2db


# ── listing ─────────────────────────────────────────────────────────────────────

def list_cards(conn, include_disposed: bool = False) -> list[dict]:
    """Unified graded + raw card list for the collection table."""
    out = []
    g_where = "" if include_disposed else "WHERE status='active'"
    for r in conn.execute(f"SELECT * FROM graded_cards {g_where}"):
        basis = r["acquisition_price"] or 0.0
        mv = r["market_value"]
        out.append({
            "table": "graded_cards", "id": r["id"], "kind": "slab",
            "name": r["card_name"], "set": r["set_name"] or "",
            "number": r["card_number"] or "", "year": r["year"] or "",
            "company": r["grading_company"] or "", "grade": r["grade"] or "",
            "cert": r["serial_number"] or "",
            "acq_cost": basis, "basis": basis,
            "basis_unknown": bool(r["basis_unknown"]),
            "market_value": mv,
            # gain is unknowable without a basis — don't invent one
            "gain": (None if r["basis_unknown"] or mv is None
                     else round(mv - basis, 2)),
            "repriced": r["market_value_updated"] or "",
            "status": r["status"], "acq_date": r["acquisition_date"] or "",
            "notes": r["notes"] or "",
        })
    u_where = ("WHERE status IN ('active','submitted_for_grading')"
               if not include_disposed else "WHERE status != 'promoted'")
    for r in conn.execute(f"SELECT * FROM ungraded_cards {u_where}"):
        basis = r["purchase_price"] or 0.0
        mv = r["market_value"]
        out.append({
            "table": "ungraded_cards", "id": r["id"], "kind": "raw",
            "name": r["card_name"], "set": r["set_name"] or "",
            "number": r["card_number"] or "", "year": r["year"] or "",
            "company": "", "grade": "", "cert": "",
            "acq_cost": basis, "basis": basis,
            "basis_unknown": bool(r["basis_unknown"]),
            "market_value": mv,
            "gain": (None if r["basis_unknown"] or mv is None
                     else round(mv - basis, 2)),
            "repriced": r["market_value_updated"] or "",
            "status": r["status"],
            "acq_date": r["purchase_date"] or "",
            "notes": r["notes"] or "",
            "grading_status": r["grading_status"],
            "target_company": r["target_grading_company"] or "",
        })
    return out


# ── repricing ───────────────────────────────────────────────────────────────────

def reprice(conn, card_id: int, market_value: float | None,
            table: str = "graded_cards"):
    """Update a card's market value; stamps market_value_updated. For slabs it
    also appends to price_history (which feeds Top Movers) — price_history is
    keyed to graded_cards, so raw repricing updates the value only."""
    if table not in ("graded_cards", "ungraded_cards"):
        raise ValueError(f"unknown table {table!r}")
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        f"UPDATE {table} SET market_value=?, market_value_updated=? WHERE id=?",
        (market_value, now if market_value is not None else None, card_id))
    if cur.rowcount == 0:
        # no such card — without this, the price_history insert below would
        # blow up on its foreign key while holding an open write transaction
        conn.rollback()
        raise ValueError(f"card id={card_id} not found in {table}")
    if market_value is not None and table == "graded_cards":
        conn.execute(
            "INSERT INTO price_history (card_id, recorded_at, market_value) VALUES (?,?,?)",
            (card_id, now, market_value))
    conn.commit()


# ── raw → graded promotion (grading cost rolls into basis) ─────────────────────

def promote_raw(conn, ungraded_id: int, *, grading_company: str, grade: str,
                serial_number: str, grading_cost: float,
                return_date: str | None = None) -> int:
    """Promote a raw card to graded. Basis = raw purchase price + grading cost."""
    raw = conn.execute("SELECT * FROM ungraded_cards WHERE id=?", (ungraded_id,)).fetchone()
    if raw is None:
        raise ValueError(f"ungraded card id={ungraded_id} not found")
    if raw["status"] not in ("active", "submitted_for_grading"):
        raise ValueError(f"card {raw['card_name']!r} has status={raw['status']!r} — cannot promote")

    return_date = return_date or date.today().isoformat()
    grading_cost = round(float(grading_cost or 0), 2)
    basis = round((raw["purchase_price"] or 0.0) + grading_cost, 2)

    cur = conn.execute(
        """INSERT INTO graded_cards
             (serial_number, grading_company, grade, card_name, card_number,
              set_name, year, photo_filename, acquisition_type,
              acquisition_price, grading_fee, trade_value, trade_details,
              acquisition_date, notes, date_added, status, acquired_via_deal_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', ?)""",
        (serial_number, grading_company, grade, raw["card_name"],
         raw["card_number"], raw["set_name"], raw["year"] or "",
         raw["photo_filename"], raw["acquisition_type"] or "Cash",
         basis, grading_cost, raw["trade_value"] or 0.0,
         raw["trade_details"] or "", return_date, raw["notes"] or "",
         datetime.now().isoformat(), raw["acquired_via_deal_id"]))
    graded_id = cur.lastrowid

    conn.execute(
        "UPDATE ungraded_cards SET status='promoted', is_converted=1 WHERE id=?",
        (ungraded_id,))
    conn.commit()
    return graded_id


def crack_to_raw(conn, graded_id: int, *, target_company: str = "PSA",
                 grading_status: str = "Slated", note: str = "") -> int:
    """Reverse of promote_raw: the slab was cracked open, so the card goes back
    to the raw section (e.g. to be resubmitted to another grader).

    Basis carries over intact — cracking doesn't change what was paid, and any
    grading fee already sunk into acquisition_price stays in the new raw basis.
    The graded row is preserved as history with status='cracked'; it is NOT a
    disposal (nothing sold, no proceeds, no realized gain), so it stays out of
    realized-gains reporting while dropping out of active inventory.
    """
    g = conn.execute("SELECT * FROM graded_cards WHERE id=?", (graded_id,)).fetchone()
    if g is None:
        raise ValueError(f"graded card id={graded_id} not found")
    if g["status"] != "active":
        raise ValueError(f"card {g['card_name']!r} has status={g['status']!r} — "
                         "only active slabs can be cracked")

    basis = g["acquisition_price"] or 0.0
    trail = (f"Cracked out of {g['grading_company']} {g['grade']} slab"
             + (f" (cert {g['serial_number']})" if (g["serial_number"] or "").strip() else "")
             + f" on {date.today().isoformat()}")
    notes = " | ".join(x for x in [(g["notes"] or "").strip(), trail, note.strip()] if x)

    cur = conn.execute(
        """INSERT INTO ungraded_cards
             (card_name, card_number, set_name, year, photo_filename,
              purchase_price, purchase_date, acquisition_type, trade_value,
              trade_details, notes, grading_status, target_grading_company,
              date_added, status, acquired_via_deal_id,
              market_value, market_value_updated, basis_unknown)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, ?, ?, ?, ?, ?)""",
        (g["card_name"], g["card_number"], g["set_name"], g["year"] or "",
         g["photo_filename"], basis, g["acquisition_date"],
         g["acquisition_type"] or "Cash", g["trade_value"] or 0.0,
         g["trade_details"] or "", notes, grading_status, target_company,
         datetime.now().isoformat(),
         "submitted_for_grading" if grading_status == "At Grading" else "active",
         g["acquired_via_deal_id"],
         g["market_value"], g["market_value_updated"], g["basis_unknown"]))
    raw_id = cur.lastrowid

    conn.execute("UPDATE graded_cards SET status='cracked' WHERE id=?", (graded_id,))
    conn.commit()
    return raw_id


def set_grading_status(conn, ungraded_id: int, grading_status: str):
    status = "submitted_for_grading" if grading_status == "At Grading" else "active"
    conn.execute(
        "UPDATE ungraded_cards SET grading_status=?, status=? "
        "WHERE id=? AND status IN ('active','submitted_for_grading')",
        (grading_status, status, ungraded_id))
    if grading_status == "At Grading":
        # stamp submission time once (kept if already set)
        conn.execute(
            "UPDATE ungraded_cards SET submitted_at=COALESCE(submitted_at, ?) "
            "WHERE id=? AND status='submitted_for_grading'",
            (datetime.now().isoformat(timespec="seconds"), ungraded_id))
    else:
        conn.execute(
            "UPDATE ungraded_cards SET submitted_at=NULL "
            "WHERE id=? AND status IN ('active','submitted_for_grading')", (ungraded_id,))
    conn.commit()


# ── dashboard ───────────────────────────────────────────────────────────────────

def dashboard_stats(conn) -> dict:
    g = conn.execute("SELECT * FROM graded_cards WHERE status='active'").fetchall()
    r = conn.execute("SELECT * FROM ungraded_cards WHERE status IN "
                     "('active','submitted_for_grading')").fetchall()

    g_market = sum((c["market_value"] if c["market_value"] is not None
                    else c["acquisition_price"] or 0) for c in g)
    r_basis  = sum(c["purchase_price"] or 0 for c in r)
    # raw uses its market value where one has been entered, else carries at cost
    r_market = sum((c["market_value"] if c["market_value"] is not None
                    else c["purchase_price"] or 0) for c in r)
    basis    = sum(c["acquisition_price"] or 0 for c in g) + r_basis
    market   = g_market + r_market
    # unrealized gain only over cards whose cost is actually known — a
    # basis_unknown card's market value is real portfolio worth but its
    # "gain" is unknowable, not 100%
    unreal = sum(((c["market_value"] if c["market_value"] is not None
                   else c["acquisition_price"] or 0) - (c["acquisition_price"] or 0))
                 for c in g if not c["basis_unknown"]) \
           + sum(((c["market_value"] if c["market_value"] is not None
                   else c["purchase_price"] or 0) - (c["purchase_price"] or 0))
                 for c in r if not c["basis_unknown"])

    y0 = f"{date.today().year}-01-01"
    realized_ytd = (conn.execute(
        "SELECT COALESCE(SUM(realized_gain),0) FROM graded_cards "
        "WHERE realized_gain IS NOT NULL AND disposed_at >= ?", (y0,)).fetchone()[0]
        + conn.execute(
        "SELECT COALESCE(SUM(realized_gain),0) FROM ungraded_cards "
        "WHERE realized_gain IS NOT NULL AND disposed_at >= ?", (y0,)).fetchone()[0])
    realized_all = (conn.execute(
        "SELECT COALESCE(SUM(realized_gain),0) FROM graded_cards "
        "WHERE realized_gain IS NOT NULL").fetchone()[0]
        + conn.execute(
        "SELECT COALESCE(SUM(realized_gain),0) FROM ungraded_cards "
        "WHERE realized_gain IS NOT NULL").fetchone()[0])

    return {
        "cards_active": len(g) + len(r),
        "slabs": len(g), "raw": len(r),
        "market": round(market, 2),
        "basis": round(basis, 2),
        "unrealized": round(unreal, 2),
        "realized_ytd": round(realized_ytd, 2),
        "realized_all": round(realized_all, 2),
    }


def dashboard_extras(conn) -> dict:
    """Company breakdown, top holdings, attention counts, snapshot delta."""
    comp = []
    total_mkt = 0.0
    for co in v2db.GRADING_COMPANIES:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(market_value, acquisition_price)),0) AS mkt "
            "FROM graded_cards WHERE status='active' AND grading_company=?", (co,)).fetchone()
        if row["n"]:
            comp.append({"company": co, "count": row["n"], "market": round(row["mkt"], 2)})
            total_mkt += row["mkt"]
    for c in comp:
        c["share"] = round(c["market"] / total_mkt * 100, 1) if total_mkt else 0
    comp.sort(key=lambda c: -c["market"])

    top = []
    for r in conn.execute(
            "SELECT * FROM graded_cards WHERE status='active' AND market_value IS NOT NULL "
            "ORDER BY market_value DESC LIMIT 5"):
        basis = r["acquisition_price"] or 0
        top.append({"name": r["card_name"], "company": r["grading_company"],
                    "grade": r["grade"], "set": r["set_name"] or "",
                    "market": r["market_value"],
                    "gain": (None if r["basis_unknown"]
                             else round(r["market_value"] - basis, 2))})

    cutoff = (datetime.now().date().toordinal() - 30)
    stale = 0
    for r in conn.execute("SELECT market_value_updated FROM graded_cards WHERE status='active'"):
        ts = r["market_value_updated"]
        if not ts:
            stale += 1
            continue
        try:
            if date.fromisoformat(ts[:10]).toordinal() < cutoff:
                stale += 1
        except ValueError:
            stale += 1
    at_grading = conn.execute(
        "SELECT COUNT(*) FROM ungraded_cards WHERE status='submitted_for_grading'").fetchone()[0]
    never_priced = conn.execute(
        "SELECT COUNT(*) FROM graded_cards WHERE status='active' AND market_value IS NULL").fetchone()[0]

    snaps = conn.execute(
        "SELECT * FROM portfolio_snapshots ORDER BY snapshot_date DESC LIMIT 2").fetchall()
    delta = None
    if len(snaps) == 2:
        delta = {"amount": round(snaps[0]["total_value"] - snaps[1]["total_value"], 2),
                 "since": snaps[1]["snapshot_date"]}

    return {"companies": comp, "top": top, "stale": stale,
            "at_grading": at_grading, "never_priced": never_priced, "delta": delta}


# ── cash ledger ─────────────────────────────────────────────────────────────────

def add_cash_entry(conn, amount: float, memo: str = "", occurred_at: str | None = None):
    conn.execute(
        "INSERT INTO cash_ledger (occurred_at, amount, memo) VALUES (?,?,?)",
        (occurred_at or datetime.now().isoformat(timespec="seconds"),
         round(float(amount), 2), memo))
    conn.commit()


def cash_summary(conn, days: int = 30) -> dict:
    """Current balance (ledger + all deal cash) and last-N-days flow."""
    ledger = conn.execute("SELECT COALESCE(SUM(amount),0) FROM cash_ledger").fetchone()[0]
    deals = conn.execute("SELECT COALESCE(SUM(cash_amount),0) FROM deals").fetchone()[0]
    cutoff = date.fromordinal(date.today().toordinal() - days).isoformat()
    flow_in = conn.execute(
        "SELECT COALESCE(SUM(cash_amount),0) FROM deals WHERE cash_amount>0 AND occurred_at>=?",
        (cutoff,)).fetchone()[0] + conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM cash_ledger WHERE amount>0 AND occurred_at>=?",
        (cutoff,)).fetchone()[0]
    flow_out = conn.execute(
        "SELECT COALESCE(SUM(cash_amount),0) FROM deals WHERE cash_amount<0 AND occurred_at>=?",
        (cutoff,)).fetchone()[0] + conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM cash_ledger WHERE amount<0 AND occurred_at>=?",
        (cutoff,)).fetchone()[0]
    recent = conn.execute(
        "SELECT * FROM cash_ledger ORDER BY occurred_at DESC, id DESC LIMIT 5").fetchall()
    return {"balance": round(ledger + deals, 2),
            "flow_in": round(flow_in, 2), "flow_out": round(abs(flow_out), 2),
            "net": round(flow_in + flow_out, 2), "days": days,
            "recent": [dict(r) for r in recent],
            "has_ledger": conn.execute("SELECT COUNT(*) FROM cash_ledger").fetchone()[0] > 0}


# ── dashboard: trade stats / movers / grading / sell candidates ────────────────

def trade_stats(conn) -> dict:
    rows = conn.execute(
        "SELECT realized_gain FROM graded_cards WHERE realized_gain IS NOT NULL "
        "UNION ALL SELECT realized_gain FROM ungraded_cards WHERE realized_gain IS NOT NULL"
    ).fetchall()
    gains = [r["realized_gain"] for r in rows]
    wins = sum(1 for g in gains if g > 0)
    # win rate over decided outcomes only — even trades ($0 gain, e.g. trade
    # settlements at cost) are excluded from the denominator
    decided = [g for g in gains if g != 0]
    n_deals = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    return {"moved": len(gains), "decided": len(decided),
            "win_rate": round(wins / len(decided) * 100, 1) if decided else None,
            "avg_profit": round(sum(gains) / len(gains), 2) if gains else None,
            "deals": n_deals}


def top_movers(conn, limit: int = 5) -> list[dict]:
    """Cards whose two most recent price points differ — biggest $ moves first."""
    movers = []
    cards = conn.execute(
        "SELECT id, card_name, grading_company, grade FROM graded_cards "
        "WHERE status='active'").fetchall()
    for c in cards:
        pts = conn.execute(
            "SELECT market_value, recorded_at FROM price_history WHERE card_id=? "
            "ORDER BY recorded_at DESC, id DESC LIMIT 2", (c["id"],)).fetchall()
        if len(pts) == 2 and pts[0]["market_value"] != pts[1]["market_value"]:
            delta = round(pts[0]["market_value"] - pts[1]["market_value"], 2)
            movers.append({"name": c["card_name"], "company": c["grading_company"],
                           "grade": c["grade"], "now": pts[0]["market_value"],
                           "prev": pts[1]["market_value"], "delta": delta,
                           "pct": round(delta / pts[1]["market_value"] * 100, 1)
                                  if pts[1]["market_value"] else None})
    movers.sort(key=lambda m: -abs(m["delta"]))
    return movers[:limit]


def at_grading(conn) -> list[dict]:
    out = []
    today = date.today()
    for r in conn.execute("SELECT * FROM ungraded_cards WHERE status='submitted_for_grading'"):
        days = None
        if r["submitted_at"]:
            try:
                days = (today - date.fromisoformat(r["submitted_at"][:10])).days
            except ValueError:
                pass
        out.append({"id": r["id"], "name": r["card_name"],
                    "target": r["target_grading_company"] or "—", "days": days})
    return out


def sell_candidates(conn) -> dict:
    """How many active slabs are profitable sold at 85% / 88% of market.

    Cards with an unknown basis are excluded — without a cost there is no
    knowable profit, and counting proceeds as pure profit would overstate it.
    """
    res = {}
    for pct in (85, 88):
        n, profit = 0, 0.0
        for r in conn.execute(
                "SELECT acquisition_price, market_value FROM graded_cards "
                "WHERE status='active' AND market_value IS NOT NULL "
                "AND basis_unknown=0"):
            p = r["market_value"] * pct / 100 - (r["acquisition_price"] or 0)
            if p > 0:
                n += 1
                profit += p
        res[pct] = {"count": n, "profit": round(profit, 2)}
    return res


def record_snapshot(conn):
    """Upsert today's portfolio snapshot (feeds the value-over-time chart)."""
    stats = dashboard_stats(conn)
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT id FROM portfolio_snapshots WHERE snapshot_date=?", (today,)).fetchone()
    if row:
        conn.execute("UPDATE portfolio_snapshots SET total_value=?, card_count=? WHERE id=?",
                     (stats["market"], stats["cards_active"], row["id"]))
    else:
        conn.execute("INSERT INTO portfolio_snapshots (snapshot_date, total_value, card_count) "
                     "VALUES (?,?,?)", (today, stats["market"], stats["cards_active"]))
    conn.commit()


def snapshots(conn) -> list[dict]:
    return [{"date": r["snapshot_date"], "value": r["total_value"]}
            for r in conn.execute(
                "SELECT * FROM portfolio_snapshots ORDER BY snapshot_date")]
