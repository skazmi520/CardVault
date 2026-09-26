"""
CardVault v2 — deal engine.

Every transaction is a Deal with two sides plus cash:

  cards_out : cards leaving my inventory (graded or raw), each optionally
              carrying a typed per-card split value (all of them or none)
  cards_in  : cards entering my inventory (quick-entry specs), each optionally
              carrying a typed take-in price
  cash      : ONE signed amount. Positive = cash came to me. Negative = I paid.

One code path handles pure buys (cards_in + negative cash), pure sales
(cards_out + positive cash) and mixed trades.

Math (the owner's model, 2026-09-15, tightened 2026-09-25):
  * An outgoing card has a fixed basis and no sale price of its own. What it
    fetched (its proceeds) is the cash plus the take-in prices of the cards
    received, split across the outgoing cards by typed per-card values, else
    by market. Per-card values are typed on every outgoing card or on none.
  * A typed take-in is firm: it is the incoming card's basis, verbatim.
  * A blank take-in is split from the typed ask minus the cash, weighted by
    the typed markets of the incoming cards. With no ask it is refused. On a
    pure buy the blanks split the cash paid; fully typed take-ins must add up
    to the cash paid, to the cent.
  * Nothing came back (no cash, no cards in) books only as a giveaway: every
    outgoing card typed at 0. Anything else is refused, never warned-and-saved.
  * Nothing booked is ever derived from market; market only sets proportions.

  Typed and derived never share a column: basis_entered vs basis_derived on
  the card, entered_* vs derived_value in deal_lines. The request as sent is
  kept in deal_entries. resolve() is pure, so any deal can be replayed.

  A booked deal changes only through edit_deal (new revision, prior archived
  in voided_deals) or void_deal. An edit keeps the arriving cards it still
  lists and writes their name, cert and market only where the typed value
  changed, so a later reprice, cert or rename survives. Anything an undo would
  destroy is listed by later_changes() and needs confirm_lose_changes=True.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime

from . import db as v2db


# ── line specs ─────────────────────────────────────────────────────────────────

@dataclass
class CardOut:
    """A card leaving inventory. table is 'graded_cards' or 'ungraded_cards'."""
    table: str
    card_id: int
    deal_value: float | None = None


@dataclass
class CardIn:
    """A card entering inventory via quick entry.

    deal_value   = what the card counts for in this deal (for a cash buy, what
                   you paid). Drives the balance identity and becomes the basis.
    market_value = what the card is worth (optional, independent of what you
                   paid). Purely informational — never affects basis.
    """
    card_name: str
    is_graded: bool = True
    set_name: str = ""
    card_number: str = ""
    year: str = ""
    grading_company: str = ""
    grade: str = ""
    serial_number: str = ""      # cert
    deal_value: float | None = None
    market_value: float | None = None
    notes: str = ""


# ── allocation helpers ──────────────────────────────────────────────────────────

def _allocate(total: float, weights: list[float]) -> list[float]:
    """Split `total` across lines pro rata by `weights`, in whole cents.

    Largest remainder: every line gets the floor of its exact share, and the
    cents left over go one each to the lines with the largest fractions. So
    the shares sum to the total exactly, each is within a cent of its exact
    value, and none goes negative when the total is not negative."""
    n = len(weights)
    if n == 0:
        return []
    if any(w < 0 for w in weights) or sum(weights) <= 0:
        weights = [1.0] * n
    s = sum(weights)
    cents = int(round(total * 100))
    sign, cents = (-1, -cents) if cents < 0 else (1, cents)
    exact = [cents * w / s for w in weights]
    shares = [int(x) for x in exact]                 # floor: every x >= 0
    left = cents - sum(shares)
    by_fraction = sorted(range(n), key=lambda i: (-(exact[i] - shares[i]), i))
    for i in by_fraction[:left]:
        shares[i] += 1
    return [sign * c / 100 for c in shares]


def _resolve_side(line_values: list[float | None], weights: list[float],
                  side_total: float | None):
    """Resolve per-line values for one side.

    Returns (values | None, is_explicit, short). None means the side cannot be
    resolved on its own (some lines unvalued and no side total) and must be
    derived from the other side. `short` is how far the typed lines already
    exceed the side total (the blanks would get $0), else None. It is returned
    rather than kept in a module global: Flask runs threaded."""
    if not line_values:
        return [], True, None                 # empty side: trivially resolved at 0
    if all(v is not None for v in line_values):
        return [round(v, 2) for v in line_values], True, None
    if side_total is not None:
        fixed = sum(v for v in line_values if v is not None)
        remainder = round(side_total - fixed, 2)
        short = -remainder if remainder < 0 else None
        open_idx = [i for i, v in enumerate(line_values) if v is None]
        alloc = _allocate(max(0.0, remainder), [weights[i] for i in open_idx])
        out = list(line_values)
        for i, val in zip(open_idx, alloc):
            out[i] = val
        return [round(v, 2) for v in out], True, short
    return None, False, None


# ── card row access ─────────────────────────────────────────────────────────────

def _load_out_card(conn, line: CardOut) -> dict:
    """Snapshot of an outgoing card as it stands right now. Everything the
    engine needs from the card is in here, so the same snapshot (stored in
    deal_lines) replays the deal exactly, later."""
    if line.table not in ("graded_cards", "ungraded_cards"):
        raise ValueError(f"unknown table {line.table!r}")
    row = conn.execute(f"SELECT * FROM {line.table} WHERE id=?", (line.card_id,)).fetchone()
    if row is None:
        raise ValueError(f"{line.table} id={line.card_id} not found")
    if row["status"] != "active":
        raise ValueError(f"{line.table} id={line.card_id} ({row['card_name']}) "
                         f"has status={row['status']!r} — only active cards can leave in a deal")
    col = "acquisition_price" if line.table == "graded_cards" else "purchase_price"
    return {"name": row["card_name"], "basis": row[col],
            "market": row["market_value"], "market_set_at": row["market_value_updated"]}


# ── the arithmetic: pure, no database ──────────────────────────────────────────

def _out_weight(snap: dict) -> float:
    """How an outgoing card weighs in a split: its market, else its basis,
    else 1. Only ever a proportion; never booked as a value."""
    mv, basis = snap.get("market"), snap.get("basis")
    if mv and mv > 0:
        return mv
    if basis and basis > 0:
        return basis
    return 1.0


def resolve(out_snaps: list[dict], out_entered: list[float | None],
            cards_in: list[CardIn], cash_amount: float,
            out_side_total: float | None = None,
            in_side_total: float | None = None) -> dict:
    """Turn what the owner typed into what books. No database access: the deal
    screen preview, the save and tools/replay_deal.py all run exactly this.

    the owner's model (2026-09-15): an outgoing card has a fixed basis and no sale
    price of its own; what it fetched is the cash plus the take-in prices of
    the cards received. A typed take-in is firm and books verbatim. A blank
    take-in is split from the ask he typed, minus the cash. Nothing is ever
    derived from market except split proportions (2026-09-25): a blank ask
    with a blank take-in is refused rather than defaulted to his market, a
    blank market stays blank, and cash paid beyond the take-ins is refused
    rather than clamped away. Also refused: per-card values typed on some
    outgoing cards but not all; a pure buy whose typed take-ins don't add up
    to the cash paid; proceeds booked when nothing came back (only an
    all-zero giveaway may book with no cash and no cards in).

    Raises ValueError for anything that would book a number he did not
    supply or cannot see. Returns the plan."""
    warnings: list[str] = []
    cash_amount = round(float(cash_amount), 2)
    n_out = len(out_snaps)

    if not n_out and not cards_in:
        raise ValueError("a deal needs at least one card on one side")
    n_typed_out = sum(1 for v in out_entered if v is not None)
    if 0 < n_typed_out < n_out:
        raise ValueError("Type a split value on every card going out, or on none.")

    out_weights = [_out_weight(s) for s in out_snaps]
    in_mvs = [ln.market_value for ln in cards_in if (ln.market_value or 0) > 0]
    in_avg = (sum(in_mvs) / len(in_mvs)) if in_mvs else 1.0
    in_weights = [(ln.market_value if (ln.market_value or 0) > 0 else in_avg)
                  for ln in cards_in]
    in_entered = [ln.deal_value for ln in cards_in]
    out_all_typed = bool(n_out) and all(v is not None for v in out_entered)

    # the ask: only ever something typed — the ask box, or every per-card value
    ask = None
    if n_out:
        if out_side_total is not None:
            ask = round(out_side_total, 2)
        elif out_all_typed:
            ask = round(sum(out_entered), 2)

    # ── take-in prices ───────────────────────────────────────────────────────
    blanks = [ln.card_name for ln, v in zip(cards_in, in_entered) if v is None]
    if not n_out and cards_in and cash_amount < 0:
        # pure buy: the cards cost exactly the cash paid
        paid = -cash_amount
        typed_sum = round(sum(v for v in in_entered if v is not None), 2)
        if not blanks and abs(typed_sum - paid) >= 0.005:
            raise ValueError(
                f"You paid ${paid:,.2f} but the cards' prices add up to ${typed_sum:,.2f}. "
                "They must match on a pure buy, or leave some blank to split the rest.")
        if blanks and in_side_total is None and typed_sum - paid >= 0.005:
            raise ValueError(
                f"You paid ${paid:,.2f} but the prices you typed already add up to "
                f"${typed_sum:,.2f}, leaving nothing for the blank ones. Lower the "
                "typed prices or the blanks can't be split.")
    if blanks and in_side_total is None:
        if n_out and ask is None:
            raise ValueError(
                f"{', '.join(blanks)}: no price typed. Type what you took each card "
                "in at, or type what your cards went for so the blanks can be split "
                "from it. Nothing is ever split from market.")
        # blanks split what is left of the ask after the cash (a pure buy: the
        # cash paid), weighted by the market typed for each incoming card
        pool = round((ask if ask is not None else 0.0) - cash_amount, 2)
        if pool < 0:
            raise ValueError(
                f"The cash (${cash_amount:,.2f}) is more than what your cards went for "
                f"(${ask or 0:,.2f}), so there is nothing left to split across the "
                "cards left blank. Type a price for each card you're getting.")
        in_vals, _, short = _resolve_side(in_entered, in_weights, pool)
    else:
        in_vals, _, short = _resolve_side(in_entered, in_weights, in_side_total)
    if short is not None:
        raise ValueError(
            f"The prices you typed are ${short:,.2f} more than the total they "
            "split from, so the blank ones would come out at $0. Type a price for "
            "every card you're getting, or raise what your cards went for.")
    zero = [ln.card_name for ln, e, v in zip(cards_in, in_entered, in_vals)
            if e is None and v <= 0]
    if zero:
        raise ValueError(
            f"{', '.join(zero)} would come out at $0: the prices you typed and the "
            "cash already use up what your cards went for. Type a price for each.")

    # ── proceeds ─────────────────────────────────────────────────────────────
    out_vals: list[float] = []
    if n_out:
        came_back = round(cash_amount + sum(in_vals), 2)
        if cards_in or cash_amount != 0:
            if came_back < 0:
                raise ValueError(
                    f"You would pay ${-cash_amount:,.2f} cash but only take in "
                    f"${sum(in_vals):,.2f} of cards, so the cards you gave would fetch "
                    "less than nothing. Put the cash into the prices of the cards "
                    "you received.")
            split = (list(out_entered) if out_all_typed and sum(out_entered) > 0
                     else out_weights)
            out_vals = _allocate(came_back, split)
        else:
            # nothing came back: the only deal this can be is a giveaway, every
            # card typed at 0. Proceeds with no cash logged are refused.
            if not out_all_typed:
                raise ValueError(
                    "Nothing came back (no cash, no cards in). Enter the cash you "
                    "received, or for a giveaway type 0 on each card going out.")
            if any(round(v, 2) > 0 for v in out_entered):
                raise ValueError(
                    "Nothing came back, so these cards can't book proceeds. Enter the "
                    "cash you received, or type 0 on each for a giveaway.")
            out_vals = [0.0] * n_out

    v_out, v_in = round(sum(out_vals), 2), round(sum(in_vals), 2)

    # what the incoming cards cost = value given up net of cash received
    basis_pool = round(v_out - cash_amount, 2)
    if cards_in and basis_pool <= 0:
        raise ValueError(
            f"{len(cards_in)} card(s) coming in, but nothing given up and no cash paid — "
            "they would all be recorded at $0 cost basis. Enter what you paid in the "
            "Cash I pay box, or a price per card.")

    return {"ask": ask, "cash_amount": cash_amount, "warnings": warnings,
            "v_out": v_out, "v_in": v_in, "closed": v_out,
            "out_vals": out_vals, "out_weights": out_weights,
            "in_vals": in_vals, "in_weights": in_weights, "basis_pool": basis_pool}


# ── the one save path ───────────────────────────────────────────────────────────

# bumped whenever resolve() changes what it accepts: replay() skips a deal
# booked under an older version that today's rules would refuse
ENGINE_VERSION = "2026-09-25.2"

_WHEN_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S")
_WHEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")


def _normalize_occurred_at(occurred_at) -> str | None:
    """A deal's date as typed, checked and brought to seconds precision.
    Accepts only 'YYYY-MM-DD', 'YYYY-MM-DDTHH:MM' or 'YYYY-MM-DDTHH:MM:SS';
    None means now (the caller fills it in)."""
    if occurred_at is None:
        return None
    if isinstance(occurred_at, str) and _WHEN_RE.match(occurred_at):
        for fmt in _WHEN_FORMATS:
            try:
                return datetime.strptime(occurred_at, fmt).isoformat(timespec="seconds")
            except ValueError:
                continue
    raise ValueError(f"The deal date {occurred_at!r} isn't a date the app can read. "
                     "Use the date picker, or type it as YYYY-MM-DD HH:MM.")


def _same_text(a, b) -> bool:
    return str(a or "").strip() == str(b or "").strip()


def _typed_market(ln) -> float | None:
    """A line's typed market as it books: blank or non-positive is no market."""
    mv = ln.market_value if ln is not None else None
    return round(mv, 2) if (mv or 0) > 0 else None


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
              in_side_total: float | None = None,
              entry_payload: dict | None = None,
              dry_run: bool = False,
              _rebook_id: int | None = None,
              _in_card_ids: list | None = None,
              _prev_typed: list | None = None,
              _prev_occurred_at: str | None = None,
              _commit: bool = True) -> dict:
    """Save a deal atomically. Returns {deal_id, warnings, out_lines, in_lines,
    v_out, v_in, cash_amount}. Raises ValueError on invalid input; nothing is
    written when an exception is raised.

    What was typed and what was derived never share a column: a typed take-in
    lands in basis_entered, a split one in basis_derived; a blank market stays
    NULL; every line goes to deal_lines with both; entry_payload (the request
    exactly as sent) goes to deal_entries — all in one transaction.

    dry_run=True runs the same resolution and returns the plan without writing.

    Edit deal only (_in_card_ids): a kept arriving card is the same row, and
    _prev_typed holds the CardIn the previous revision recorded for it. Its
    name, set, number, year, company, grade, cert and market are written only
    where the new typed value differs from that, so a reprice, cert or rename
    made since survives. Basis and the deal-owned fields are always written."""
    import json
    cards_out, cards_in = list(cards_out), list(cards_in)
    if not cards_out and not cards_in:
        raise ValueError("a deal needs at least one card on one side")
    if payment_method not in v2db.PAYMENT_METHODS:
        raise ValueError(f"payment_method must be one of {v2db.PAYMENT_METHODS}")
    for ln in cards_in:
        if not (ln.card_name or "").strip():
            raise ValueError("every arriving card needs a name")

    occurred_at = (_normalize_occurred_at(occurred_at)
                   or datetime.now().isoformat(timespec="seconds"))
    deal_date = occurred_at[:10]

    snaps = [_load_out_card(conn, ln) for ln in cards_out]
    out_entered = [ln.deal_value for ln in cards_out]
    plan = resolve(snaps, out_entered, cards_in, cash_amount,
                   out_side_total, in_side_total)
    cash_amount = plan["cash_amount"]
    out_vals, in_vals = plan["out_vals"], plan["in_vals"]
    warnings = plan["warnings"]

    def _gain(snap, proceeds):
        return None if snap["basis"] is None else round(proceeds - snap["basis"], 2)

    if dry_run:
        plan_out = [{"table": ln.table, "card_id": ln.card_id, "name": s["name"],
                     "basis": s["basis"] or 0.0, "basis_unknown": s["basis"] is None,
                     "market_value": s["market"], "proceeds": p,
                     "realized_gain": _gain(s, p)}
                    for ln, s, p in zip(cards_out, snaps, out_vals)]
        plan_in = [{"name": ln.card_name, "agreed_value": v, "basis": v,
                    "typed": ln.deal_value is not None,
                    "market_value": ln.market_value,
                    "built_in": (round(ln.market_value - v, 2) if ln.market_value else None)}
                   for ln, v in zip(cards_in, in_vals)]
        return {"preview": True, "warnings": warnings, "v_out": plan["v_out"],
                "v_in": plan["v_in"], "cash_amount": cash_amount, "ask": plan["ask"],
                "closed": plan["closed"], "out_lines": plan_out, "in_lines": plan_in}

    # legacy-compat acquisition_type and trade_value (the trade component of a
    # card's basis, net of its share of cash paid) — derived, informational
    if cards_out and cash_amount < 0:
        acq_type = "Cash & Trade"
    elif cards_out:
        acq_type = "Trade"
    else:
        acq_type = "Cash"
    cash_paid_total = max(0.0, -cash_amount)
    cash_shares = (_allocate(min(cash_paid_total, plan["basis_pool"]),
                             [v if v > 0 else 1.0 for v in in_vals])
                   if cards_in else [])
    gave_parts = [f"{s['name']}: ${val:,.2f}" for s, val in zip(snaps, out_vals)]
    if cash_amount < 0:
        gave_parts.append(f"Cash: ${-cash_amount:,.2f}")
    trade_details = " | ".join(gave_parts)
    if cash_amount > 0 and trade_details:
        trade_details += f"  (+ ${cash_amount:,.2f} cash received)"

    now = datetime.now().isoformat(timespec="seconds")
    in_ids = list(_in_card_ids or [None] * len(cards_in))
    prev_typed = list(_prev_typed or [None] * len(cards_in))
    try:
        if _rebook_id is None:
            cur = conn.execute(
                """INSERT INTO deals (occurred_at, counterparty, location, payment_method,
                                      cash_amount, notes, created_at, ask_entered,
                                      engine_version)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (occurred_at, counterparty, location, payment_method, cash_amount, notes,
                 now, out_side_total, ENGINE_VERSION))
            deal_id = cur.lastrowid
            ref = f"deal:{deal_id}"
        else:
            # Edit deal: same deal number, new revision. edit_deal has already
            # archived and unwound the previous one inside this transaction.
            deal_id = _rebook_id
            conn.execute(
                """UPDATE deals SET occurred_at=?, counterparty=?, location=?,
                     payment_method=?, cash_amount=?, notes=?, ask_entered=?,
                     engine_version=? WHERE id=?""",
                (occurred_at, counterparty, location, payment_method, cash_amount,
                 notes, out_side_total, ENGINE_VERSION, deal_id))
            ref = f"edit:{deal_id}"

        out_lines = []
        for ln, s, entered, weight, proceeds in zip(
                cards_out, snaps, out_entered, plan["out_weights"], out_vals):
            if ln.table == "graded_cards":
                cur = conn.execute(
                    """UPDATE graded_cards SET status='disposed', disposed_at=?,
                         disposed_via_deal_id=?, disposal_proceeds=?, is_sold=1, sale_date=?
                       WHERE id=? AND status='active'""",
                    (occurred_at, deal_id, proceeds, deal_date, ln.card_id))
            else:
                cur = conn.execute(
                    """UPDATE ungraded_cards SET status='disposed', disposed_at=?,
                         disposed_via_deal_id=?, disposal_proceeds=?
                       WHERE id=? AND status='active'""",
                    (occurred_at, deal_id, proceeds, ln.card_id))
            if cur.rowcount != 1:
                # raced by another submit of the same form — abort the whole deal
                raise ValueError(
                    f"{s['name']} was already disposed (double submit?) — deal aborted")
            v2db.record_event(conn, ln.table, ln.card_id, "status", "active",
                              "disposed", "engine", ref)
            v2db.record_event(conn, ln.table, ln.card_id, "disposal_proceeds", None,
                              proceeds, "engine", ref)
            conn.execute(
                """INSERT INTO deal_lines (deal_id, side, card_table, card_id, entered_value,
                     entered_market, derived_value, basis_at_deal, market_at_deal,
                     market_set_at, weight_used) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (deal_id, "out", ln.table, ln.card_id, entered, None, proceeds,
                 s["basis"], s["market"], s["market_set_at"], weight))
            out_lines.append({"table": ln.table, "card_id": ln.card_id, "name": s["name"],
                              "basis": s["basis"] or 0.0,
                              "basis_unknown": s["basis"] is None,
                              "proceeds": proceeds, "realized_gain": _gain(s, proceeds)})

        in_lines = []
        for ln, val, weight, cash_share, keep_id, prev in zip(
                cards_in, in_vals, plan["in_weights"],
                cash_shares or [0.0] * len(cards_in), in_ids, prev_typed):
            typed = ln.deal_value is not None
            b_entered = round(ln.deal_value, 2) if typed else None
            b_derived = None if typed else val
            mv = ln.market_value if (ln.market_value or 0) > 0 else None
            trade_value = (max(0.0, round(val - cash_share, 2)) if acq_type != "Cash" else 0.0)
            seed_price = bool(ln.is_graded and mv)
            if keep_id is not None:
                # an arriving card kept across an edit: same row. The engine
                # owns the basis and deal fields; identity and market are
                # written only where the typing changed, so a rename, cert or
                # reprice made since the last revision survives the edit.
                table = "graded_cards" if ln.is_graded else "ungraded_cards"
                fields = {"basis_entered": b_entered, "basis_derived": b_derived,
                          "basis_legacy": None, "acquisition_type": acq_type,
                          "trade_value": trade_value, "trade_details": trade_details}
                typed_fields = [("card_name", "card_name"), ("set_name", "set_name"),
                                ("card_number", "card_number"), ("year", "year")]
                if ln.is_graded:
                    typed_fields += [("grading_company", "grading_company"),
                                     ("grade", "grade"), ("serial_number", "serial_number")]
                    fields["acquisition_date"] = deal_date
                else:
                    fields["purchase_date"] = deal_date
                for col, attr in typed_fields:
                    if prev is None or not _same_text(getattr(ln, attr), getattr(prev, attr)):
                        fields[col] = getattr(ln, attr)
                prev_mv = _typed_market(prev)
                market_changed = prev is None or prev_mv != _typed_market(ln)
                seed_price = seed_price and market_changed
                if market_changed:
                    fields.update(market_value=mv,
                                  market_value_updated=deal_date if mv else None)
                    if ln.is_graded and prev_mv is not None and _prev_occurred_at:
                        # replace only the point this deal seeded, never a
                        # reprice that happens to share its second
                        conn.execute(
                            "DELETE FROM price_history WHERE id = (SELECT id FROM "
                            "price_history WHERE card_id=? AND source='entered' AND "
                            "recorded_at=? AND ABS(market_value - ?) < 0.005 "
                            "ORDER BY id LIMIT 1)", (keep_id, _prev_occurred_at, prev_mv))
                v2db.update_card(conn, table, keep_id, fields, "you", ref)
                card_id = keep_id
            elif ln.is_graded:
                table = "graded_cards"
                cur = conn.execute(
                    """INSERT INTO graded_cards
                         (serial_number, grading_company, grade, card_name, card_number,
                          set_name, year, photo_filename, acquisition_type, basis_entered,
                          basis_derived, grading_fee, trade_value, trade_details,
                          acquisition_date, notes, date_added, market_value,
                          market_value_updated, status, acquired_via_deal_id)
                       VALUES (?,?,?,?,?,?,?,NULL,?,?,?,0,?,?,?,?,?,?,?,'active',?)""",
                    (ln.serial_number, ln.grading_company, ln.grade, ln.card_name,
                     ln.card_number, ln.set_name, ln.year, acq_type, b_entered, b_derived,
                     trade_value, trade_details, deal_date, ln.notes, now, mv,
                     deal_date if mv else None, deal_id))
            else:
                table = "ungraded_cards"
                cur = conn.execute(
                    """INSERT INTO ungraded_cards
                         (card_name, card_number, set_name, year, photo_filename,
                          basis_entered, basis_derived, purchase_date, acquisition_type,
                          trade_value, trade_details, notes, grading_status,
                          target_grading_company, date_added, market_value,
                          market_value_updated, status, acquired_via_deal_id)
                       VALUES (?,?,?,?,NULL,?,?,?,?,?,?,?,'Not Slated','',?,?,?,'active',?)""",
                    (ln.card_name, ln.card_number, ln.set_name, ln.year, b_entered,
                     b_derived, deal_date, acq_type, trade_value, trade_details,
                     ln.notes, now, mv, deal_date if mv else None, deal_id))
            if keep_id is None:
                card_id = cur.lastrowid
                v2db.record_event(conn, table, card_id, "created", None,
                                  json.dumps({"basis_entered": b_entered,
                                              "basis_derived": b_derived,
                                              "market_value": mv}), "engine", ref)
            if seed_price:
                # only a market the owner typed ever becomes a price point, and a
                # kept card gets a new one only when that typing changed
                conn.execute(
                    "INSERT INTO price_history (card_id, recorded_at, market_value, source) "
                    "VALUES (?,?,?, 'entered')", (card_id, occurred_at, mv))
            conn.execute(
                """INSERT INTO deal_lines (deal_id, side, card_table, card_id, entered_value,
                     entered_market, derived_value, weight_used) VALUES (?,?,?,?,?,?,?,?)""",
                (deal_id, "in", table, card_id, b_entered, mv, val, weight))
            in_lines.append({"table": table, "card_id": card_id, "name": ln.card_name,
                             "agreed_value": val, "basis": val, "typed": typed})

        # every deal keeps what it was given, so it can always be played back:
        # the screen's request verbatim, or for a direct call the arguments
        if entry_payload is None:
            entry_payload = {
                "source": "direct call", "cash_amount": cash_amount,
                "out_side_total": out_side_total, "in_side_total": in_side_total,
                "cards_out": [{"table": ln.table, "id": ln.card_id,
                               "deal_value": ln.deal_value} for ln in cards_out],
                "cards_in": [{"name": ln.card_name, "is_graded": ln.is_graded,
                              "set": ln.set_name, "number": ln.card_number,
                              "year": ln.year, "company": ln.grading_company,
                              "grade": ln.grade, "cert": ln.serial_number,
                              "deal_value": ln.deal_value,
                              "market_value": ln.market_value} for ln in cards_in]}
        conn.execute(
            "INSERT INTO deal_entries (deal_id, entered_at, payload_json) VALUES (?,?,?)",
            (deal_id, now, json.dumps(entry_payload)))
        if _commit:
            conn.commit()
    except Exception:
        if _commit:
            conn.rollback()
        raise

    return {"deal_id": deal_id, "warnings": warnings, "ask": plan["ask"],
            "closed": plan["closed"], "v_out": plan["v_out"], "v_in": plan["v_in"],
            "cash_amount": cash_amount, "out_lines": out_lines, "in_lines": in_lines}


# ── undo: void and edit ─────────────────────────────────────────────────────────

class LaterChangesError(ValueError):
    """An undo (a void, or an edit that drops an arriving line) would destroy
    changes made to the deal's arriving cards after it was booked. .changes
    lists them in plain English; retry with confirm_lose_changes=True."""

    def __init__(self, message: str, changes: list[str]):
        super().__init__(message)
        self.changes = list(changes)


_TABLES = ("graded_cards", "ungraded_cards")
_FIELD_LABELS = {
    "card_name": "name", "set_name": "set", "card_number": "number", "year": "year",
    "grading_company": "grader", "grade": "grade", "serial_number": "cert",
    "psa_cert": "PSA cert", "psa_order": "PSA order", "market_value": "market",
    "notes": "notes", "photo_filename": "photo", "is_pc": "personal collection",
    "is_favorited": "favorite", "grading_status": "grading status",
    "target_grading_company": "target grader", "expected_grade": "expected grade",
    "sub_type": "grading bet", "expected_back": "expected back", "status": "status",
    "basis_entered": "basis", "grading_fee": "grading fee",
}
_MONEY_FIELDS = {"market_value", "basis_entered", "basis_derived", "grading_fee",
                 "trade_value", "disposal_proceeds", "sale_price"}
_SOURCE_LABELS = {"you": "edited", "reprice": "reprice"}


def _deal_refs(deal_id: int) -> set[str]:
    """The card_events refs this deal's own bookings, edits and undos write."""
    return {f"{k}:{deal_id}" for k in ("deal", "edit", "void", "edited")}


def _show(field: str, v) -> str:
    if v is None or str(v).strip() == "":
        return "blank"
    if field in _MONEY_FIELDS:
        try:
            x = float(v)
            return f"{x:.0f}" if x == int(x) else f"{x:.2f}"
        except (TypeError, ValueError, OverflowError):
            pass
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "..."


def _short_date(at) -> str:
    try:
        d = datetime.fromisoformat(str(at))
        return f"{d:%b} {d.day}"
    except ValueError:
        return str(at or "")[:10]


def _created_cards(conn, deal_id: int) -> list[dict]:
    return [dict(r, _table=t) for t in _TABLES for r in conn.execute(
        f"SELECT * FROM {t} WHERE acquired_via_deal_id=? ORDER BY id", (deal_id,))]


def _seeded_points(conn, deal_id: int, deal) -> set:
    """(card_id, recorded_at, market) of every price point this deal seeded
    from a typed market, in this revision or an earlier one it replaced."""
    revisions = [(deal["occurred_at"], [dict(r) for r in conn.execute(
        "SELECT * FROM deal_lines WHERE deal_id=?", (deal_id,))])]
    for r in conn.execute("SELECT snapshot_json FROM voided_deals WHERE deal_id=? "
                          "AND reason='edited'", (deal_id,)):
        try:
            snap = json.loads(r["snapshot_json"])
            revisions.append((snap["deal"]["occurred_at"], snap.get("lines") or []))
        except (TypeError, ValueError, KeyError):
            continue
    return {(l["card_id"], occ, round(l["entered_market"], 2))
            for occ, lines in revisions for l in lines
            if l.get("side") == "in" and l.get("card_table") == "graded_cards"
            and l.get("entered_market") is not None}


def later_changes(conn, deal_id: int, cards=None) -> list[str]:
    """What an undo of this deal would destroy: changes made after it was
    booked to the cards it brought in, which a void (or an edit dropping the
    line) deletes along with the card. Card events not written by this deal's
    own booking, edits or undo and not by the engine, plus price points other
    than the one the deal seeded from a typed market. `cards` limits it to
    those (table, id) pairs. Returns plain-English lines, oldest first, e.g.
    "Giratina V: market 3500 → 3400 (reprice, Sep 24)"."""
    deal = conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
    if deal is None:
        return []
    own = _deal_refs(deal_id)
    seeded = None
    out = []
    for c in _created_cards(conn, deal_id):
        t, cid, name = c["_table"], c["id"], c["card_name"]
        if cards is not None and (t, cid) not in cards:
            continue
        events = [dict(e) for e in conn.execute(
            "SELECT * FROM card_events WHERE card_table=? AND card_id=? "
            "AND source != 'engine' ORDER BY at, id", (t, cid))
            if e["ref"] not in own]
        repriced_at = {e["at"] for e in events if e["field"] == "market_value"}
        described = set()
        for e in events:
            when = f"{_SOURCE_LABELS.get(e['source'], e['source'])}, {_short_date(e['at'])}"
            if e["field"] == "market_value_updated":
                if e["at"] not in repriced_at:
                    out.append(f"{name}: market confirmed ({when})")
                continue
            if e["field"] == "market_value" and e["new_value"] is not None:
                try:
                    described.add((e["at"], round(float(e["new_value"]), 2)))
                except ValueError:
                    pass
            label = _FIELD_LABELS.get(e["field"], e["field"].replace("_", " "))
            out.append(f"{name}: {label} {_show(e['field'], e['old_value'])} → "
                       f"{_show(e['field'], e['new_value'])} ({when})")
        if t != "graded_cards":
            continue
        for p in conn.execute("SELECT * FROM price_history WHERE card_id=? "
                              "ORDER BY recorded_at, id", (cid,)):
            mv = round(p["market_value"], 2)
            if (p["recorded_at"], mv) in described:
                continue                      # already listed as its reprice
            if seeded is None:
                seeded = _seeded_points(conn, deal_id, deal)
                legacy = conn.execute("SELECT 1 FROM deal_lines WHERE deal_id=? LIMIT 1",
                                      (deal_id,)).fetchone() is None
            if p["source"] == "entered" and (cid, p["recorded_at"], mv) in seeded:
                continue                      # the deal's own point
            if legacy and p["recorded_at"] == deal["occurred_at"]:
                continue                      # a pre-log deal's own deal-day point
            out.append(f"{name}: price point {_show('market_value', mv)} "
                       f"({_short_date(p['recorded_at'])})")
    return out


def _lose_changes_error(what: str, deal_id: int, changes: list[str]) -> LaterChangesError:
    return LaterChangesError(
        f"{what} deal #{deal_id} would throw away changes made since it was booked: "
        + "; ".join(changes) + ". Confirm to go ahead anyway, or leave the deal as it is.",
        changes)


def _blockers(conn, deal_id: int) -> list[str]:
    """Cards this deal brought in that have since moved on (sold, graded,
    cracked, lost): undoing the deal would orphan their history."""
    out = []
    for c in _created_cards(conn, deal_id):
        if c["status"] in ("promoted", "cracked"):
            out.append(f"{c['card_name']} (came in via this deal, since graded or cracked)")
        elif c["status"] not in ("active", "submitted_for_grading"):
            out.append(f"{c['card_name']} (came in via this deal, now {c['status']})")
    return out


def _refuse_if_blocked(conn, deal_id: int):
    blockers = _blockers(conn, deal_id)
    if blockers:
        raise ValueError(
            "cannot change this deal — cards from it have moved on: "
            + "; ".join(blockers)
            + ". Correct those first (their history would be orphaned).")


def _unbook(conn, deal_id: int, reason: str, keep_in: frozenset = frozenset()) -> dict:
    """Archive a deal in voided_deals, then put the world back as it was before
    it: outgoing cards return to active, and arriving cards are deleted with
    their price points, except those in keep_in, which an edit updates in
    place (save_deal replaces their deal-seeded price point only if the typed
    market changed). Never commits: void_deal and edit_deal own the
    transaction, and check later_changes() before calling this.

    Refused unless it is fully safe: every card the deal created must still be
    held, so the undo cannot orphan later history."""
    deal = conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
    if deal is None:
        raise ValueError(f"deal {deal_id} not found")
    _refuse_if_blocked(conn, deal_id)

    ins = [c for c in _created_cards(conn, deal_id)
           if c["status"] not in ("promoted", "cracked")]
    outs = [dict(r, _table=t) for t in _TABLES for r in conn.execute(
        f"SELECT * FROM {t} WHERE disposed_via_deal_id=?", (deal_id,))]

    def rows(sql, *args):
        return [dict(r) for r in conn.execute(sql, args)]

    snapshot = {
        "deal": dict(deal),
        "lines": rows("SELECT * FROM deal_lines WHERE deal_id=?", deal_id),
        "entries": rows("SELECT * FROM deal_entries WHERE deal_id=?", deal_id),
        "photos": rows("SELECT * FROM deal_photos WHERE deal_id=?", deal_id),
        "cards_in": ins, "cards_out": outs,
        "price_history": [p for c in ins if c["_table"] == "graded_cards"
                          for p in rows("SELECT * FROM price_history WHERE card_id=?",
                                        c["id"])],
    }
    ref = f"{reason}:{deal_id}"
    conn.execute(
        "INSERT INTO voided_deals (deal_id, voided_at, reason, snapshot_json) VALUES (?,?,?,?)",
        (deal_id, datetime.now().isoformat(timespec="seconds"), reason,
         json.dumps(snapshot, default=str)))
    for c in ins:
        if (c["_table"], c["id"]) in keep_in:
            continue
        if c["_table"] == "graded_cards":
            conn.execute("DELETE FROM price_history WHERE card_id=?", (c["id"],))
        conn.execute(f"DELETE FROM {c['_table']} WHERE id=?", (c["id"],))
        v2db.record_event(conn, c["_table"], c["id"], "deleted", c["status"], None,
                          "you", ref)
    for c in outs:
        fields = {"status": "active", "disposed_at": None, "disposed_via_deal_id": None,
                  "disposal_proceeds": None}
        if c["_table"] == "graded_cards":
            fields.update(is_sold=0, sale_price=None, sale_date=None)
        v2db.update_card(conn, c["_table"], c["id"], fields, "you", ref)
    conn.execute("DELETE FROM deal_lines WHERE deal_id=?", (deal_id,))
    return {"deal": deal, "ins": ins, "outs": outs}


def void_deal(conn, deal_id: int, confirm_lose_changes: bool = False) -> dict:
    """Undo a mistakenly-entered deal: the full record is archived in
    voided_deals, cards that left come back, cards it created are removed, and
    the deal row goes away. If those cards were changed since (repriced,
    renamed, a cert added) the void is refused with LaterChangesError listing
    what would be lost, unless confirm_lose_changes=True."""
    try:
        if conn.execute("SELECT 1 FROM deals WHERE id=?", (deal_id,)).fetchone() is None:
            raise ValueError(f"deal {deal_id} not found")
        _refuse_if_blocked(conn, deal_id)
        changes = later_changes(conn, deal_id)
        if changes and not confirm_lose_changes:
            raise _lose_changes_error("Voiding", deal_id, changes)
        res = _unbook(conn, deal_id, "void")
        conn.execute("DELETE FROM deal_photos WHERE deal_id=?", (deal_id,))
        conn.execute("DELETE FROM deals WHERE id=?", (deal_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"deal_id": deal_id,
            "restored": [c["card_name"] for c in res["outs"]],
            "removed": [c["card_name"] for c in res["ins"]],
            "cash_reversed": res["deal"]["cash_amount"],
            "lost_changes": changes}


def _editable(conn, deal_id: int) -> bool:
    """Only a deal with an entry log and per-line records has typing to edit."""
    return (conn.execute("SELECT 1 FROM deal_entries WHERE deal_id=? LIMIT 1",
                         (deal_id,)).fetchone() is not None
            and conn.execute("SELECT 1 FROM deal_lines WHERE deal_id=? LIMIT 1",
                             (deal_id,)).fetchone() is not None)


def _previous_typing(conn, deal_id: int) -> dict:
    """{(table, id): CardIn} — what the current revision recorded as typed for
    each arriving card: the latest deal_entries payload, paired in order with
    the deal_lines 'in' rows (which carry the card each line created)."""
    from .app import _parse_deal_payload
    if not _editable(conn, deal_id):
        raise ValueError(
            f"Deal #{deal_id} was booked before the entry log, so there is nothing "
            "typed to edit. Void it and book it again if it's wrong.")
    entry = conn.execute("SELECT payload_json FROM deal_entries WHERE deal_id=? "
                         "ORDER BY id DESC LIMIT 1", (deal_id,)).fetchone()
    in_lines = conn.execute("SELECT card_table, card_id FROM deal_lines WHERE deal_id=? "
                            "AND side='in' ORDER BY id", (deal_id,)).fetchall()
    try:
        prev_ins = _parse_deal_payload(json.loads(entry["payload_json"]))[1]
    except (ValueError, TypeError, KeyError, AttributeError):
        prev_ins = None
    if prev_ins is None or len(prev_ins) != len(in_lines):
        raise ValueError(
            f"Deal #{deal_id}'s entry log doesn't line up with the cards it booked, "
            "so an edit can't tell what you typed for each. Void it and book it "
            "again if it's wrong.")
    return {(l["card_table"], l["card_id"]): p for l, p in zip(in_lines, prev_ins)}


def edit_deal(conn, deal_id: int, *, cards_out: list[CardOut] = (),
              cards_in: list[CardIn] = (), in_card_refs: list | None = None,
              dry_run: bool = False, confirm_lose_changes: bool = False, **kw) -> dict:
    """Edit deal: the only way a booked deal changes. In one transaction the
    current version is archived (voided_deals, reason 'edited'), unwound, and
    the new version booked under the same deal number, with what was typed
    appended to deal_entries. in_card_refs pairs each arriving line with the
    (table, id) of the card it already created, or None for a new card.

    A kept card stays the same row and keeps what was changed on it since
    (see save_deal). A card whose line is dropped is deleted, so if it was
    changed since, the edit raises LaterChangesError unless
    confirm_lose_changes=True. Refused outright: a deal with no entry log, a
    kept card switching between slab and raw, two lines on one card.

    dry_run previews the edit and rolls everything back; instead of raising
    LaterChangesError it lists them in warnings and "later_changes"."""
    deal = conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
    if deal is None:
        raise ValueError(f"deal {deal_id} not found")
    if "occurred_at" in kw:
        kw["occurred_at"] = _normalize_occurred_at(kw["occurred_at"])
    prev_by_card = _previous_typing(conn, deal_id)

    cards_in = list(cards_in)
    refs = list(in_card_refs or [None] * len(cards_in))
    if len(refs) != len(cards_in):
        raise ValueError("in_card_refs must pair with cards_in")
    existing = {(c["_table"], c["id"]): c for c in _created_cards(conn, deal_id)}
    keep_ids, seen = [], set()
    for ln, ref in zip(cards_in, refs):
        if ref is None:
            keep_ids.append(None)                 # a new card
            continue
        ref = tuple(ref)
        card = existing.get(ref)
        if card is None:
            raise ValueError(
                f"{ln.card_name or 'A line'} points at a card that didn't come in on "
                f"deal #{deal_id}. Remove the line and add it again as a new card.")
        name = card["card_name"]
        if ref[0] != ("graded_cards" if ln.is_graded else "ungraded_cards"):
            raise ValueError(
                f"{name}: can't change a card between slab and raw in an edit; "
                "remove the line and add a new one.")
        if ref in seen:
            raise ValueError(f"Two lines point at the same card ({name}). "
                             "Remove one of them.")
        if ref not in prev_by_card:
            raise ValueError(
                f"{name}: deal #{deal_id} has no record of what you typed for it, so "
                "an edit can't tell what changed. Remove the line and add a new one.")
        seen.add(ref)
        keep_ids.append(ref)
    dropped = {k for k, c in existing.items()
               if k not in seen and c["status"] not in ("promoted", "cracked")}
    try:
        _refuse_if_blocked(conn, deal_id)
        changes = later_changes(conn, deal_id, cards=dropped)
        if changes and not confirm_lose_changes and not dry_run:
            raise _lose_changes_error("Saving this edit of", deal_id, changes)
        _unbook(conn, deal_id, "edited", frozenset(seen))
        res = save_deal(conn, cards_out=cards_out, cards_in=cards_in,
                        dry_run=dry_run, _rebook_id=None if dry_run else deal_id,
                        _in_card_ids=[k[1] if k else None for k in keep_ids],
                        _prev_typed=[prev_by_card[k] if k else None for k in keep_ids],
                        _prev_occurred_at=deal["occurred_at"],
                        _commit=False, **kw)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    res["later_changes"] = changes
    if dry_run and changes:
        res["warnings"] = list(res["warnings"]) + [
            "Saving this edit throws away changes made since the deal was booked: "
            + "; ".join(changes)]
    return res


def replay(conn, deal_id: int) -> dict:
    """Play a deal back: re-run resolve() on exactly what was typed (the last
    deal_entries row) with the outgoing cards as they stood that day (the
    deal_lines snapshot), and compare every derived number with what is
    stored. Returns {"ok": bool, "diffs": [...], "skipped": reason|None}.

    A deal booked under an older engine whose typing today's rules refuse is
    skipped with the reason; under the current engine that is a diff."""
    from .app import _card_ref, _parse_deal_payload, _parse_money
    deal = conn.execute("SELECT cash_amount, engine_version FROM deals WHERE id=?",
                        (deal_id,)).fetchone()
    entry = conn.execute("SELECT payload_json FROM deal_entries WHERE deal_id=? "
                         "ORDER BY id DESC LIMIT 1", (deal_id,)).fetchone()
    lines = [dict(r) for r in conn.execute(
        "SELECT * FROM deal_lines WHERE deal_id=? ORDER BY id", (deal_id,))]
    if deal is None or entry is None or not lines:
        return {"ok": True, "diffs": [], "skipped": "no entry log or lines (pre-2026-09-25)"}
    p = json.loads(entry["payload_json"])
    if p.get("quick_sale"):
        # the quick-sale box: one card out at the typed price, that cash in
        table, cid = _card_ref(p, "card_id")
        price = _parse_money(p.get("price"), "price")
        outs, ins, cash, out_total, in_total = [CardOut(table, cid, price)], [], price, None, None
    else:
        outs, ins, cash, out_total, in_total = _parse_deal_payload(p)
    out_lines = [l for l in lines if l["side"] == "out"]
    in_lines = [l for l in lines if l["side"] == "in"]
    diffs = []
    if len(out_lines) != len(outs) or len(in_lines) != len(ins):
        return {"ok": False, "skipped": None, "diffs": [
            f"line count: typed {len(outs)} out / {len(ins)} in, stored "
            f"{len(out_lines)} / {len(in_lines)}"]}
    snaps = [{"name": f"{l['card_table']}#{l['card_id']}", "basis": l["basis_at_deal"],
              "market": l["market_at_deal"], "market_set_at": l["market_set_at"]}
             for l in out_lines]
    try:
        plan = resolve(snaps, [o.deal_value for o in outs], ins, cash, out_total, in_total)
    except ValueError as e:
        if deal["engine_version"] != ENGINE_VERSION:
            return {"ok": True, "diffs": [], "skipped":
                    f"booked under engine {deal['engine_version']}; today's rules refuse "
                    f"what was typed ({e})"}
        return {"ok": False, "skipped": None, "diffs": [f"replay refused: {e}"]}

    def near(a, b):
        return a is not None and b is not None and abs(a - b) < 0.005

    if not near(round(cash, 2), deal["cash_amount"]):
        diffs.append(f"cash: typed {round(cash, 2)}, deal {deal['cash_amount']}")

    for o, l, v in zip(outs, out_lines, plan["out_vals"]):
        if (o.table, o.card_id) != (l["card_table"], l["card_id"]):
            diffs.append(f"out card order: typed {o.table}#{o.card_id}, stored "
                         f"{l['card_table']}#{l['card_id']}")
        if not near(v, l["derived_value"]):
            diffs.append(f"out {l['card_table']}#{l['card_id']}: replay {v}, line {l['derived_value']}")
        row = conn.execute(f"SELECT disposal_proceeds FROM {l['card_table']} WHERE id=?",
                           (l["card_id"],)).fetchone()
        if row is None or not near(v, row["disposal_proceeds"]):
            diffs.append(f"out {l['card_table']}#{l['card_id']}: replay {v}, card "
                         f"{row['disposal_proceeds'] if row else 'missing'}")
    for ln, l, v in zip(ins, in_lines, plan["in_vals"]):
        if not near(v, l["derived_value"]):
            diffs.append(f"in {ln.card_name}: replay {v}, line {l['derived_value']}")
        typed = ln.deal_value
        if (typed is None) != (l["entered_value"] is None) or (
                typed is not None and not near(typed, l["entered_value"])):
            diffs.append(f"in {ln.card_name}: typed {typed}, line entered {l['entered_value']}")
        row = conn.execute(f"SELECT basis_entered, basis_derived, market_value FROM "
                           f"{l['card_table']} WHERE id=?", (l["card_id"],)).fetchone()
        if row is None:
            diffs.append(f"in {ln.card_name}: card {l['card_table']}#{l['card_id']} missing")
            continue
        want_e, want_d = (typed, None) if typed is not None else (None, v)
        if not ((want_e is None and row["basis_entered"] is None) or near(want_e, row["basis_entered"])):
            diffs.append(f"in {ln.card_name}: basis_entered {row['basis_entered']}, typed {want_e}")
        if not ((want_d is None and row["basis_derived"] is None) or near(want_d, row["basis_derived"])):
            diffs.append(f"in {ln.card_name}: basis_derived {row['basis_derived']}, replay {want_d}")
    return {"ok": not diffs, "diffs": diffs, "skipped": None}


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
        # promote/crack copies acquired_via_deal_id onto the successor row and
        # leaves it on the superseded one — count each card once, not twice
        inn += [dict(r, _table=table) for r in conn.execute(
            f"SELECT * FROM {table} WHERE acquired_via_deal_id=? "
            f"AND status NOT IN ('promoted','cracked')", (deal_id,))]
    # The deal page is a record of that day, not of today. Deals booked since
    # schema 7 carry deal_lines: what was typed and derived per line, and the
    # outgoing card's market as it stood when it left. Older deals fall back
    # to what survives, and say "unknown" rather than guess: a price_history
    # row the old engine copied from the take-in is not a market, and a stored
    # market set after the card left is not the market then.
    lines = {(r["side"], r["card_table"], r["card_id"]): dict(r) for r in conn.execute(
        "SELECT * FROM deal_lines WHERE deal_id=?", (deal_id,))}
    occurred = deal["occurred_at"]
    for c in inn:
        ln = lines.get(("in", c["_table"], c["id"]))
        c["line"] = ln
        c["market_then"] = None
        c["market_note"] = ""
        if ln is not None:
            c["market_then"] = ln["entered_market"]
            if ln["entered_market"] is None:
                c["market_note"] = "unknown: no market was typed"
        elif c["_table"] == "graded_cards":
            row = conn.execute(
                "SELECT market_value, source FROM price_history WHERE card_id=? "
                "AND substr(recorded_at,1,10)=? ORDER BY recorded_at, id LIMIT 1",
                (c["id"], (occurred or "")[:10])).fetchone()
            if row is not None and row["source"] == "copied":
                c["market_note"] = "unknown: the old app copied the take-in here"
            elif row is not None:
                c["market_then"] = row["market_value"]
    for c in out:
        ln = lines.get(("out", c["_table"], c["id"]))
        c["line"] = ln
        c["market_then"] = None
        c["market_note"] = ""
        if ln is not None:
            c["market_then"] = ln["market_at_deal"]
            c["market_set_at"] = ln["market_set_at"]
            if ln["market_at_deal"] is None:
                c["market_note"] = "unknown: no market on record when it left"
        elif c["_table"] == "graded_cards" and c["market_value"] is not None:
            upd, left = (c["market_value_updated"] or ""), (c["disposed_at"] or "")
            if upd and left and upd[:10] > left[:10]:
                c["market_note"] = "unknown: repriced after it left"
            else:
                c["market_then"] = c["market_value"]
                c["market_set_at"] = c["market_value_updated"]
    photos = conn.execute(
        "SELECT * FROM deal_photos WHERE deal_id=?", (deal_id,)).fetchall()
    revisions = conn.execute(
        "SELECT COUNT(*) FROM voided_deals WHERE deal_id=? AND reason='edited'",
        (deal_id,)).fetchone()[0]
    return {"deal": deal, "cards_out": out, "cards_in": inn, "photos": photos,
            "entered": deal_as_entered(conn, deal_id), "revisions": revisions,
            "editable": _editable(conn, deal_id)}


def deal_as_entered(conn, deal_id: int) -> dict | None:
    """What the owner actually typed on the deal screen, kept verbatim in
    deal_entries at save time. The whole deal-37 investigation stalled on one
    question — "did I type $400 or did the engine invent $700?" — and nothing
    had kept the answer. Now it is stored, and shown on the deal page."""
    import json
    import sqlite3
    try:
        row = conn.execute(
            "SELECT entered_at, payload_json FROM deal_entries WHERE deal_id=? "
            "ORDER BY id DESC LIMIT 1", (deal_id,)).fetchone()
    except sqlite3.OperationalError:          # database predates the table
        return None
    if row is None:
        return None
    try:
        p = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None
    out = []
    for x in p.get("cards_out", []):
        t, i = x.get("table"), x.get("id")
        name = ""
        if t in ("graded_cards", "ungraded_cards") and i is not None:
            r = conn.execute(f"SELECT card_name FROM {t} WHERE id=?", (i,)).fetchone()
            name = r["card_name"] if r else f"{t}#{i}"
        out.append({"name": name, "typed": _num(x.get("deal_value"))})
    inn = [{"name": x.get("name", ""), "typed": _num(x.get("deal_value")),
            "market": _num(x.get("market_value"))} for x in p.get("cards_in", [])
           if any(str(x.get(k) or "").strip()
                  for k in ("name", "deal_value", "market_value", "cert"))]
    if "cash_received" in p or "cash_paid" in p:
        cash = round((_num(p.get("cash_received")) or 0) - (_num(p.get("cash_paid")) or 0), 2)
    else:
        cash = _num(p.get("cash_amount"))
    return {"entered_at": row["entered_at"], "ask": _num(p.get("out_side_total")),
            "cash": cash, "out": out, "in": inn}


def _num(v):
    """A typed box from a stored payload as a number for display (None if blank)."""
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except ValueError:
        return None
