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
            "market_value": mv,
            "gain": (round(mv - basis, 2) if mv is not None else None),
            "repriced": r["market_value_updated"] or "",
            "status": r["status"], "acq_date": r["acquisition_date"] or "",
        })
    u_where = ("WHERE status IN ('active','submitted_for_grading')"
               if not include_disposed else "WHERE status != 'promoted'")
    for r in conn.execute(f"SELECT * FROM ungraded_cards {u_where}"):
        basis = r["purchase_price"] or 0.0
        out.append({
            "table": "ungraded_cards", "id": r["id"], "kind": "raw",
            "name": r["card_name"], "set": r["set_name"] or "",
            "number": r["card_number"] or "", "year": r["year"] or "",
            "company": "", "grade": "", "cert": "",
            "acq_cost": basis, "basis": basis,
            "market_value": None, "gain": None, "repriced": "",
            "status": r["status"],
            "acq_date": r["purchase_date"] or "",
            "grading_status": r["grading_status"],
            "target_company": r["target_grading_company"] or "",
        })
    return out


# ── repricing ───────────────────────────────────────────────────────────────────

def reprice(conn, card_id: int, market_value: float | None):
    """Update a graded card's market value; stamps market_value_updated."""
    conn.execute(
        "UPDATE graded_cards SET market_value=?, market_value_updated=? WHERE id=?",
        (market_value,
         datetime.now().isoformat(timespec="seconds") if market_value is not None else None,
         card_id))
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


def set_grading_status(conn, ungraded_id: int, grading_status: str):
    status = "submitted_for_grading" if grading_status == "At Grading" else "active"
    conn.execute(
        "UPDATE ungraded_cards SET grading_status=?, status=? WHERE id=? AND status != 'promoted'",
        (grading_status, status, ungraded_id))
    conn.commit()


# ── dashboard ───────────────────────────────────────────────────────────────────

def dashboard_stats(conn) -> dict:
    g = conn.execute("SELECT * FROM graded_cards WHERE status='active'").fetchall()
    r = conn.execute("SELECT * FROM ungraded_cards WHERE status IN "
                     "('active','submitted_for_grading')").fetchall()

    g_market = sum((c["market_value"] if c["market_value"] is not None
                    else c["acquisition_price"] or 0) for c in g)
    r_basis  = sum(c["purchase_price"] or 0 for c in r)
    basis    = sum(c["acquisition_price"] or 0 for c in g) + r_basis
    market   = g_market + r_basis          # raw carries at cost (no market data)

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
        "unrealized": round(market - basis, 2),
        "realized_ytd": round(realized_ytd, 2),
        "realized_all": round(realized_all, 2),
    }


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
