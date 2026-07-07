"""
CardVault v2 — deal engine.

Every transaction is a Deal with two sides plus cash:

  cards_out : cards leaving my inventory (graded or raw), each optionally
              carrying an agreed per-line deal value
  cards_in  : cards entering my inventory (quick-entry specs), each optionally
              carrying an agreed per-line deal value
  cash      : ONE signed amount. Positive = cash came to me. Negative = I paid.

One code path handles pure buys (cards_in + negative cash), pure sales
(cards_out + positive cash) and mixed trades.

Math (per spec):
  V_out = resolved total value of the cards-out side
  V_in  = resolved total value of the cards-in side
  Balance identity: V_out = V_in + cash_amount

  * Per-line deal values are used when entered.
  * A side with only a side-total gets it allocated pro rata across its
    unvalued lines (weights: current market value, falling back to basis,
    falling back to equal).
  * A side with no values at all is derived from the other side via the
    balance identity.
  * When BOTH sides are fully itemized they should reconcile; a mismatch
    greater than 5% produces a warning but the deal still saves.

  Cards out: disposal_proceeds = allocated value
             realized_gain     = disposal_proceeds - full basis
             disposed_at       = deal timestamp, status = 'disposed'
             (rows are never deleted)
  Cards in : basis = allocated share of (V_out - cash_amount)
             i.e. value of cards given up plus cash paid, net of cash received
             market_value seeded with the agreed line value
"""

from dataclasses import dataclass
from datetime import datetime

from . import db as v2db

RECONCILE_TOLERANCE = 0.05   # warn when itemized sides differ by more than 5%


# ── line specs ─────────────────────────────────────────────────────────────────

@dataclass
class CardOut:
    """A card leaving inventory. table is 'graded_cards' or 'ungraded_cards'."""
    table: str
    card_id: int
    deal_value: float | None = None


@dataclass
class CardIn:
    """A card entering inventory via quick entry."""
    card_name: str
    is_graded: bool = True
    set_name: str = ""
    card_number: str = ""
    year: str = ""
    grading_company: str = ""
    grade: str = ""
    serial_number: str = ""      # cert
    deal_value: float | None = None
    notes: str = ""


# ── allocation helpers ──────────────────────────────────────────────────────────

def _allocate(total: float, weights: list[float]) -> list[float]:
    """Split `total` across lines pro rata by `weights`, penny-exact."""
    n = len(weights)
    if n == 0:
        return []
    s = sum(weights)
    if s <= 0:
        weights, s = [1.0] * n, float(n)
    vals = [round(total * w / s, 2) for w in weights]
    vals[-1] = round(vals[-1] + (round(total, 2) - round(sum(vals), 2)), 2)
    return vals


def _resolve_side(line_values: list[float | None], weights: list[float],
                  side_total: float | None):
    """Resolve per-line values for one side.

    Returns (values | None, is_explicit). None means the side cannot be
    resolved on its own (some lines unvalued and no side total) and must be
    derived from the other side.
    """
    if not line_values:
        return [], True                       # empty side: trivially resolved at 0
    if all(v is not None for v in line_values):
        return [round(v, 2) for v in line_values], True
    if side_total is not None:
        fixed = sum(v for v in line_values if v is not None)
        remainder = round(side_total - fixed, 2)
        open_idx = [i for i, v in enumerate(line_values) if v is None]
        alloc = _allocate(max(0.0, remainder), [weights[i] for i in open_idx])
        out = list(line_values)
        for i, val in zip(open_idx, alloc):
            out[i] = val
        return [round(v, 2) for v in out], True
    return None, False


def _fill_from_total(line_values: list[float | None], weights: list[float],
                     implied_total: float) -> list[float]:
    """Fill unvalued lines from an implied side total (keeps entered values)."""
    vals, _ = _resolve_side(line_values, weights, implied_total)
    return vals


# ── card row access ─────────────────────────────────────────────────────────────

def _load_out_card(conn, line: CardOut) -> dict:
    if line.table not in ("graded_cards", "ungraded_cards"):
        raise ValueError(f"unknown table {line.table!r}")
    row = conn.execute(f"SELECT * FROM {line.table} WHERE id=?", (line.card_id,)).fetchone()
    if row is None:
        raise ValueError(f"{line.table} id={line.card_id} not found")
    if row["status"] != "active":
        raise ValueError(f"{line.table} id={line.card_id} ({row['card_name']}) "
                         f"has status={row['status']!r} — only active cards can leave in a deal")
    if line.table == "graded_cards":
        basis = row["acquisition_price"] or 0.0
    else:
        basis = row["purchase_price"] or 0.0
    mv = row["market_value"] if line.table == "graded_cards" else None
    return {"row": row, "basis": basis,
            "weight": (mv if mv and mv > 0 else basis if basis > 0 else 1.0),
            "name": row["card_name"]}


# ── the one save path ───────────────────────────────────────────────────────────

def save_deal(conn, *,
              cards_out: list[CardOut] = (),
              cards_in: list[CardIn] = (),
              cash_amount: float = 0.0,
              occurred_at: str | None = None,
              counterparty: str = "",
              location: str = "",
              payment_method: str = "cash",
              notes: str = "",
              out_side_total: float | None = None,
              in_side_total: float | None = None) -> dict:
    """Save a deal atomically. Returns {deal_id, warnings, out_lines, in_lines,
    v_out, v_in, cash_amount}. Raises ValueError on invalid input; nothing is
    written when an exception is raised."""
    cards_out, cards_in = list(cards_out), list(cards_in)
    warnings: list[str] = []

    if not cards_out and not cards_in:
        raise ValueError("a deal needs at least one card on one side")
    if payment_method not in v2db.PAYMENT_METHODS:
        raise ValueError(f"payment_method must be one of {v2db.PAYMENT_METHODS}")

    occurred_at = occurred_at or datetime.now().isoformat(timespec="seconds")
    deal_date = occurred_at[:10]
    cash_amount = round(float(cash_amount), 2)

    # ── load + validate out cards ────────────────────────────────────────────
    out_info = [_load_out_card(conn, ln) for ln in cards_out]
    out_vals_entered = [ln.deal_value for ln in cards_out]
    out_weights = [i["weight"] for i in out_info]

    in_vals_entered = [ln.deal_value for ln in cards_in]
    in_weights = [1.0] * len(cards_in)   # incoming cards have no stored market value

    # ── resolve side values ──────────────────────────────────────────────────
    out_vals, out_ok = _resolve_side(out_vals_entered, out_weights, out_side_total)
    in_vals,  in_ok  = _resolve_side(in_vals_entered,  in_weights,  in_side_total)

    if out_ok and in_ok:
        v_out, v_in = sum(out_vals), sum(in_vals)
        # reconcile fully-itemized sides: V_out should equal V_in + cash
        expected_out = v_in + cash_amount
        if cards_out and cards_in:
            denom = max(abs(v_out), abs(expected_out), 0.01)
            if abs(v_out - expected_out) / denom > RECONCILE_TOLERANCE:
                warnings.append(
                    f"Sides don't reconcile: cards-out total ${v_out:,.2f} vs "
                    f"cards-in + cash ${expected_out:,.2f} "
                    f"(diff ${abs(v_out - expected_out):,.2f}, >5%). Saving anyway.")
        elif cards_out and not cards_in and abs(v_out - cash_amount) > 0.01 and cash_amount != 0:
            warnings.append(
                f"Pure sale: cards-out total ${v_out:,.2f} differs from cash received "
                f"${cash_amount:,.2f}.")
        elif cards_in and not cards_out and abs(v_in + cash_amount) > 0.01 and cash_amount != 0:
            warnings.append(
                f"Pure buy: cards-in total ${v_in:,.2f} differs from cash paid "
                f"${-cash_amount:,.2f}.")
    elif out_ok and not in_ok:
        v_out = sum(out_vals)
        v_in = round(v_out - cash_amount, 2)          # balance identity
        if v_in < 0:
            warnings.append("Derived cards-in value was negative; clamped to $0.")
            v_in = 0.0
        in_vals = _fill_from_total(in_vals_entered, in_weights, v_in)
    elif in_ok and not out_ok:
        v_in = sum(in_vals)
        v_out = round(v_in + cash_amount, 2)          # balance identity
        if v_out < 0:
            warnings.append("Derived cards-out value was negative; clamped to $0.")
            v_out = 0.0
        out_vals = _fill_from_total(out_vals_entered, out_weights, v_out)
    else:
        raise ValueError(
            "cannot resolve deal values: both sides have unvalued lines and no side totals")

    v_out, v_in = round(sum(out_vals), 2), round(sum(in_vals), 2)

    # ── basis pool for incoming cards ────────────────────────────────────────
    # what the incoming cards cost me = value given up net of cash received
    basis_pool = round(v_out - cash_amount, 2)
    if cards_in and basis_pool < 0:
        warnings.append("Cards-in basis pool was negative; clamped to $0.")
        basis_pool = 0.0
    in_basis = _allocate(basis_pool, [v if v > 0 else 1.0 for v in in_vals]) \
        if cards_in else []

    # ── legacy-compat acquisition_type for incoming cards ────────────────────
    if cards_out and cash_amount < 0:
        acq_type = "Cash & Trade"
    elif cards_out:
        acq_type = "Trade"
    else:
        acq_type = "Cash"
    gave_parts = [f"{i['name']}: ${val:,.2f}" for i, val in zip(out_info, out_vals)]
    if cash_amount < 0:
        gave_parts.append(f"Cash: ${-cash_amount:,.2f}")
    trade_details = " | ".join(gave_parts)
    if cash_amount > 0:
        trade_details += f"  (+ ${cash_amount:,.2f} cash received)" if trade_details else ""

    # ── write everything in one transaction ──────────────────────────────────
    try:
        cur = conn.execute(
            """INSERT INTO deals (occurred_at, counterparty, location,
                                  payment_method, cash_amount, notes, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (occurred_at, counterparty, location, payment_method,
             cash_amount, notes, datetime.now().isoformat(timespec="seconds")))
        deal_id = cur.lastrowid

        out_lines = []
        for ln, info, proceeds in zip(cards_out, out_info, out_vals):
            gain = round(proceeds - info["basis"], 2)
            if ln.table == "graded_cards":
                conn.execute(
                    """UPDATE graded_cards SET
                         status='disposed', disposed_at=?, disposed_via_deal_id=?,
                         disposal_proceeds=?, realized_gain=?,
                         is_sold=1, sale_price=?, sale_date=?
                       WHERE id=?""",
                    (occurred_at, deal_id, proceeds, gain, proceeds, deal_date, ln.card_id))
            else:
                conn.execute(
                    """UPDATE ungraded_cards SET
                         status='disposed', disposed_at=?, disposed_via_deal_id=?,
                         disposal_proceeds=?, realized_gain=?
                       WHERE id=?""",
                    (occurred_at, deal_id, proceeds, gain, ln.card_id))
            out_lines.append({"table": ln.table, "card_id": ln.card_id,
                              "name": info["name"], "basis": info["basis"],
                              "proceeds": proceeds, "realized_gain": gain})

        in_lines = []
        for ln, agreed, basis in zip(cards_in, in_vals, in_basis):
            if ln.is_graded:
                cur = conn.execute(
                    """INSERT INTO graded_cards
                         (serial_number, grading_company, grade, card_name,
                          card_number, set_name, year, photo_filename,
                          acquisition_type, acquisition_price, grading_fee,
                          trade_value, trade_details, acquisition_date, notes,
                          date_added, market_value, market_value_updated,
                          status, acquired_via_deal_id)
                       VALUES (?,?,?,?,?,?,?,NULL,?,?,0,?,?,?,?,?,?,?, 'active', ?)""",
                    (ln.serial_number, ln.grading_company, ln.grade, ln.card_name,
                     ln.card_number, ln.set_name, ln.year,
                     acq_type, basis,
                     (v_out if acq_type != "Cash" else 0.0), trade_details,
                     deal_date, ln.notes, datetime.now().isoformat(),
                     (agreed if agreed > 0 else None),
                     (deal_date if agreed > 0 else None),
                     deal_id))
            else:
                cur = conn.execute(
                    """INSERT INTO ungraded_cards
                         (card_name, card_number, set_name, year, photo_filename,
                          purchase_price, purchase_date, acquisition_type,
                          trade_value, trade_details, notes, grading_status,
                          target_grading_company, date_added,
                          status, acquired_via_deal_id)
                       VALUES (?,?,?,?,NULL,?,?,?,?,?,?, 'Not Slated', '', ?, 'active', ?)""",
                    (ln.card_name, ln.card_number, ln.set_name, ln.year,
                     basis, deal_date, acq_type,
                     (v_out if acq_type != "Cash" else 0.0), trade_details,
                     ln.notes, datetime.now().isoformat(), deal_id))
            if ln.is_graded and agreed > 0:
                # seed price history so movers/repricing have a baseline
                conn.execute(
                    "INSERT INTO price_history (card_id, recorded_at, market_value) "
                    "VALUES (?,?,?)", (cur.lastrowid, occurred_at, agreed))
            in_lines.append({"table": "graded_cards" if ln.is_graded else "ungraded_cards",
                             "card_id": cur.lastrowid, "name": ln.card_name,
                             "agreed_value": agreed, "basis": basis})

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {"deal_id": deal_id, "warnings": warnings,
            "v_out": v_out, "v_in": v_in, "cash_amount": cash_amount,
            "out_lines": out_lines, "in_lines": in_lines}


# ── photos ──────────────────────────────────────────────────────────────────────

def add_deal_photo(conn, deal_id: int, file_path: str, captured_at: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO deal_photos (deal_id, file_path, captured_at) VALUES (?,?,?)",
        (deal_id, file_path, captured_at))
    conn.commit()
    return cur.lastrowid


# ── queries ─────────────────────────────────────────────────────────────────────

def list_deals(conn, date_from: str | None = None, date_to: str | None = None):
    sql, args = "SELECT * FROM deals", []
    conds = []
    if date_from:
        conds.append("occurred_at >= ?"); args.append(date_from)
    if date_to:
        conds.append("occurred_at <= ?"); args.append(date_to + "T23:59:59")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    return conn.execute(sql + " ORDER BY occurred_at DESC", args).fetchall()


def get_deal(conn, deal_id: int) -> dict | None:
    deal = conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
    if deal is None:
        return None
    out, inn = [], []
    for table in ("graded_cards", "ungraded_cards"):
        out += [dict(r, _table=table) for r in conn.execute(
            f"SELECT * FROM {table} WHERE disposed_via_deal_id=?", (deal_id,))]
        inn += [dict(r, _table=table) for r in conn.execute(
            f"SELECT * FROM {table} WHERE acquired_via_deal_id=?", (deal_id,))]
    photos = conn.execute(
        "SELECT * FROM deal_photos WHERE deal_id=?", (deal_id,)).fetchall()
    return {"deal": deal, "cards_out": out, "cards_in": inn, "photos": photos}
