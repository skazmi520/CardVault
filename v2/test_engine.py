"""
CardVault v2 — deal engine regression tests.

Unlike test_deals.py (which prints a worked example for eyeballing), every test
here ASSERTS and fails loudly. Each one pins down a rule that was broken at some
point, so the same bug cannot come back quietly.

Runs against a throwaway database built from the schema v2/db.py defines
(v2db.scratch_database). The live database is never opened.

Run:  python3 -m v2.test_engine
"""

import json
import sqlite3
import sys

from . import cards as v2cards
from . import db as v2db
from . import deals as v2deals
from .deals import (CardIn, CardOut, LaterChangesError, _allocate, edit_deal, get_deal,
                    later_changes, replay, save_deal, void_deal)

FAILURES = []
PASSES = []


def check(name, cond, detail=""):
    (PASSES if cond else FAILURES).append(name)
    print(f"  {'ok    ' if cond else 'FAIL  '} {name}" + (f"\n           {detail}" if not cond and detail else ""))


def fresh_db():
    """A throwaway database built from the schema this code defines (v2/db.py),
    never copied from the live database — these tests must never touch it."""
    return v2db.scratch_database(prefix="cv_engine_")


def slab(c, name, basis, mv, unknown=0):
    cur = c.execute(
        "INSERT INTO graded_cards (card_name, grading_company, grade, basis_entered, "
        "acquisition_date, market_value, status, date_added, notes, set_name, card_number) "
        "VALUES (?,'PSA','10',?,'2026-01-01',?,'active','2026-01-01','','','')",
        (name, None if unknown else basis, mv))
    c.commit()
    return cur.lastrowid


def refused(c, name, fn, needle="", exc=ValueError):
    """Assert fn raises exc (message containing needle) and writes no deal."""
    before = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    try:
        fn()
    except exc as e:
        after = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        check(name, needle in str(e) and after == before,
              f"message {str(e)!r} (want {needle!r}); deals {before} -> {after}")
        return e
    check(name, False, "no error raised")
    return None


def basis_cols(c, table, cid):
    row = c.execute(f"SELECT basis_entered, basis_derived FROM {table} WHERE id=?", (cid,)).fetchone()
    return row["basis_entered"], row["basis_derived"]


def proceeds_of(c, cid):
    return c.execute("SELECT disposal_proceeds FROM graded_cards WHERE id=?", (cid,)).fetchone()[0]


def gain_of(c, cid):
    return c.execute("SELECT realized_gain FROM graded_cards WHERE id=?", (cid,)).fetchone()[0]


# ── the rules ──────────────────────────────────────────────────────────────────

def test_pure_sale_scales_to_cash(c):
    """Sold at a discount: proceeds must equal the cash, not the market prefill.
    This is the Buyer bug — $X of market booked against $X received."""
    a, b = slab(c, "A", 800, 1000), slab(c, "B", 800, 1000)
    save_deal(c, cards_out=[CardOut("graded_cards", a, 1000.0), CardOut("graded_cards", b, 1000.0)],
              cash_amount=1600.0, occurred_at="2026-02-01T00:00:00")
    tot = proceeds_of(c, a) + proceeds_of(c, b)
    check("pure sale scales proceeds to cash received", abs(tot - 1600.0) < 0.01,
          f"proceeds {tot} should be 1600")
    check("pure sale recomputes gain from scaled proceeds", abs(gain_of(c, a) - 0.0) < 0.01,
          f"gain {gain_of(c, a)} should be 0 (800 proceeds - 800 basis)")


def test_pure_sale_scales_with_an_unvalued_line(c):
    """Found by the fuzzer, not by us. A single blank give-line sent the deal
    down a different resolution branch where entered values above the cash were
    kept as-is — booking $6,467 of proceeds against $5,405 received.
    2026-09-25 (E6): a split typed on some cards but not all is now refused
    outright, so this shape can no longer book at all."""
    a, b = slab(c, "A1", 800, 1000), slab(c, "B1", 800, 1000)
    refused(c, "a split value typed on one card out but not the other is refused",
            lambda: save_deal(c, cards_out=[CardOut("graded_cards", a, 4000.0),
                                            CardOut("graded_cards", b, None)],   # <- blank
                              cash_amount=1500.0, occurred_at="2026-02-01T00:00:00"),
            "every card going out, or on none")


def test_zero_cash_is_not_scaled(c):
    """Zero cash is ambiguous — giveaway, or a sale whose cash was not logged.
    It used to warn and book the typed values as proceeds with no cash behind
    them. 2026-09-25 (E7): refused; only an all-zero giveaway books."""
    a = slab(c, "C", 500, 900)
    refused(c, "zero-cash sale with a positive typed value is refused",
            lambda: save_deal(c, cards_out=[CardOut("graded_cards", a, 900.0)],
                              cash_amount=0.0, occurred_at="2026-02-01T00:00:00"),
            "Nothing came back")
    status = c.execute("SELECT status FROM graded_cards WHERE id=?", (a,)).fetchone()[0]
    check("the refused card is still held", status == "active", f"status {status}")


def test_unknown_basis_books_no_gain(c):
    """No recorded cost means no knowable gain. Booking proceeds-minus-zero
    inflates lifetime profit with money that was never earned."""
    a = slab(c, "D", 0, 700, unknown=1)
    save_deal(c, cards_out=[CardOut("graded_cards", a, 700.0)],
              cash_amount=700.0, occurred_at="2026-02-01T00:00:00")
    check("unknown basis books proceeds but no gain",
          proceeds_of(c, a) == 700.0 and gain_of(c, a) is None,
          f"proceeds {proceeds_of(c, a)}, gain {gain_of(c, a)} (gain should be None)")


def test_cash_received_reduces_basis_flowing_in(c):
    """Give a $6,100 card, take back $3,000 cash plus a card: the card carries
    $3,100 of basis, not $6,100. Getting this sign backwards inflated a Lugia's
    traced basis from $X to $X."""
    a = slab(c, "E", 6100, 6100)
    save_deal(c, cards_out=[CardOut("graded_cards", a, 6100.0)],
              cards_in=[CardIn(card_name="F", deal_value=3100.0)],
              cash_amount=3000.0, occurred_at="2026-02-01T00:00:00")
    got = c.execute("SELECT acquisition_price FROM graded_cards WHERE card_name='F'").fetchone()[0]
    check("cash received reduces the basis of cards taken in", abs(got - 3100.0) < 0.01,
          f"incoming basis {got} should be 3100")


def test_agreed_price_beats_market_everywhere(c):
    """the owner's rule: 'I take a card in at 4k, trade it away for 5k, it should
    just book as +1000' — market value never substitutes for an agreed price.

    Deal 47 broke this: a $6,300-market Gyarados traded for a $5,000 Mario
    booked +$1,900 of gain and $6,300 of basis on the Mario. The agreed price
    was $5,000 on both sides."""
    a = slab(c, "GyaradosLike", 4400, 6300)          # market well above the agreed price
    save_deal(c, cards_out=[CardOut("graded_cards", a, 5000.0)],
              cards_in=[CardIn(card_name="MarioLike", deal_value=5000.0,
                               market_value=8000.0)],   # reference only
              cash_amount=0.0, occurred_at="2026-02-01T00:00:00")
    check("out card books the agreed price, not its market",
          abs(proceeds_of(c, a) - 5000.0) < 0.01,
          f"proceeds {proceeds_of(c, a)} should be 5000, not 6300")
    check("gain is agreed price minus basis", abs(gain_of(c, a) - 600.0) < 0.01,
          f"gain {gain_of(c, a)} should be +600, not +1900")
    got = c.execute("SELECT acquisition_price FROM graded_cards WHERE card_name='MarioLike'").fetchone()[0]
    check("incoming basis is the agreed price, not its market value",
          abs(got - 5000.0) < 0.01, f"basis {got} should be 5000, not 8000")


def test_blank_in_lines_split_pro_rata_by_market(c):
    """the owner's big-deal workflow: give $9,000, take in six cards worth
    $10,490, type only their markets and leave every paid $ blank. The $9,000
    must split across them by market — the old equal split would have put the
    same basis on a $590 Latios as a $3,500 Articuno."""
    a = slab(c, "PGSlike", 9000, 10000)
    mvs = {"L1": 590.0, "L2": 750.0, "R1": 350.0, "S1": 3100.0, "U1": 2200.0, "A1x": 3500.0}
    save_deal(c, cards_out=[CardOut("graded_cards", a, 9000.0)],
              cards_in=[CardIn(card_name=n, deal_value=None, market_value=mv)
                        for n, mv in mvs.items()],
              cash_amount=0.0, occurred_at="2026-02-01T00:00:00")
    tot_mv = sum(mvs.values())
    got, want_ok = {}, True
    for n, mv in mvs.items():
        b = c.execute("SELECT acquisition_price FROM graded_cards WHERE card_name=?", (n,)).fetchone()[0]
        got[n] = b
        if abs(b - 9000.0 * mv / tot_mv) > 0.02:
            want_ok = False
    total = round(sum(got.values()), 2)
    check("blank in-lines split the deal total pro rata by market",
          want_ok and abs(total - 9000.0) < 0.01,
          f"got {got}, total {total}")


def in_basis_of(c, name):
    return c.execute(
        "SELECT acquisition_price FROM graded_cards WHERE card_name=? "
        "UNION ALL SELECT purchase_price FROM ungraded_cards WHERE card_name=?",
        (name, name)).fetchone()[0]


def test_deal_37_books_under_stefans_model(c):
    """Deal 37 (2026-08-08), the one that broke trust. Gave a Rayquaza V with a
    $1,292.25 basis, put out at $1,450; took back $750 cash and a Mega
    Charizard Y taken in at $400. What the Rayquaza fetched is $750 + $400 =
    $1,150, a $142.25 loss. The old engine booked $1,450 of proceeds and a
    $158 gain, and back-solved the Charizard to $700 of cost. No warning is
    wanted: taking in less than you gave is a choice, not a mistake."""
    a = slab(c, "RayqLike", 1292.25, 1450)
    r = save_deal(c, cards_out=[CardOut("graded_cards", a, None)],
                  cards_in=[CardIn(card_name="MCYtyped", deal_value=400.0, market_value=400.0)],
                  cash_amount=750.0, out_side_total=1450.0, occurred_at="2026-08-08T14:36:00")
    check("proceeds are cash + take-in, not the ask", abs(proceeds_of(c, a) - 1150.0) < 0.01,
          f"proceeds {proceeds_of(c, a)} should be 1150")
    check("the loss books", abs(gain_of(c, a) - (-142.25)) < 0.01, f"gain {gain_of(c, a)} should be -142.25")
    check("typed take-in is the basis", abs(in_basis_of(c, "MCYtyped") - 400.0) < 0.01)
    check("no warning for taking in less than you gave", not r["warnings"], f"warnings: {r['warnings']}")
    check("the ask is reported, not booked", r["ask"] == 1450.0 and r["closed"] == 1150.0,
          f"ask {r['ask']} closed {r['closed']}")

    # the same deal with the take-in left blank: the ask fills it, and the
    # deal balances by construction — that IS what was entered, so no warning
    b = slab(c, "RayqLike2", 1292.25, 1450)
    r2 = save_deal(c, cards_out=[CardOut("graded_cards", b, None)],
                   cards_in=[CardIn(card_name="MCYblank", deal_value=None, market_value=400.0)],
                   cash_amount=750.0, out_side_total=1450.0, occurred_at="2026-08-08T14:36:00")
    check("blank take-in is filled from the ask minus cash", abs(in_basis_of(c, "MCYblank") - 700.0) < 0.01)
    check("blank take-in deal is silent", not r2["warnings"], f"warnings: {r2['warnings']}")


def test_worked_example_umbreon(c):
    """The example the owner signed off on 2026-09-15. Give Umbreon Ex (basis
    $5,500, market $6,000). Take in Charizard ex at $1,275 (market $1,500) and
    Tohoku's Pikachu at $1,615 (market $1,900), plus $3,000 cash. The Umbreon
    fetched $5,890 → realized +$390. The $510 of margin on the new cards is
    unrealized, carried as market minus basis until they sell."""
    u = slab(c, "UmbreonLike", 5500, 6000)
    r = save_deal(c, cards_out=[CardOut("graded_cards", u, None)],
                  cards_in=[CardIn(card_name="ZardLike", deal_value=1275.0, market_value=1500.0),
                            CardIn(card_name="TohokuLike", deal_value=1615.0, market_value=1900.0)],
                  cash_amount=3000.0, out_side_total=6000.0, occurred_at="2026-09-15T12:00:00")
    check("Umbreon fetched cash + take-in = 5,890", abs(proceeds_of(c, u) - 5890.0) < 0.01, f"{proceeds_of(c, u)}")
    check("realized +390", abs(gain_of(c, u) - 390.0) < 0.01, f"{gain_of(c, u)}")
    check("take-ins book verbatim", abs(in_basis_of(c, "ZardLike") - 1275.0) < 0.01
          and abs(in_basis_of(c, "TohokuLike") - 1615.0) < 0.01)
    built = sum(l["built_in"] for l in save_deal(
        c, cards_out=[], cards_in=[CardIn(card_name="x", deal_value=1275.0, market_value=1500.0),
                                   CardIn(card_name="y", deal_value=1615.0, market_value=1900.0)],
        cash_amount=-2890.0, dry_run=True)["in_lines"])
    check("$510 built in is reported as unrealized, not realized", abs(built - 510.0) < 0.01, f"{built}")
    check("no warnings on a normal trade", not r["warnings"], f"warnings: {r['warnings']}")


def test_two_cards_out_split_by_typed_values(c):
    """Two cards out, proceeds split by the typed per-card values — they set
    the split, never the total."""
    a, b = slab(c, "A2", 100, 1000), slab(c, "B2", 100, 3000)
    save_deal(c, cards_out=[CardOut("graded_cards", a, 1000.0), CardOut("graded_cards", b, 3000.0)],
              cards_in=[CardIn(card_name="Cin", deal_value=2000.0)],
              cash_amount=0.0, occurred_at="2026-09-15T12:00:00")
    check("proceeds total what came back", abs(proceeds_of(c, a) + proceeds_of(c, b) - 2000.0) < 0.01)
    check("split follows the typed values 1:3", abs(proceeds_of(c, a) - 500.0) < 0.01
          and abs(proceeds_of(c, b) - 1500.0) < 0.01, f"{proceeds_of(c, a)} / {proceeds_of(c, b)}")


def test_double_disposal_refused(c):
    """A card already sold cannot leave again — that would double-count proceeds."""
    a = slab(c, "G", 100, 200)
    save_deal(c, cards_out=[CardOut("graded_cards", a, 200.0)], cash_amount=200.0,
              occurred_at="2026-02-01T00:00:00")
    try:
        save_deal(c, cards_out=[CardOut("graded_cards", a, 200.0)], cash_amount=200.0,
                  occurred_at="2026-02-02T00:00:00")
        check("second disposal of the same card is refused", False, "no error raised")
    except ValueError:
        check("second disposal of the same card is refused", True)


def test_allocation_is_penny_exact(c):
    """Three-way split of an odd number must still total exactly."""
    ids = [slab(c, f"H{i}", 100, 1000) for i in range(3)]
    save_deal(c, cards_out=[CardOut("graded_cards", i, 1000.0) for i in ids],
              cash_amount=1000.0, occurred_at="2026-02-01T00:00:00")
    tot = sum(proceeds_of(c, i) for i in ids)
    check("allocation is penny-exact", abs(tot - 1000.0) < 0.005, f"total {tot} should be 1000.00")


def test_empty_deal_refused(c):
    try:
        save_deal(c, cash_amount=100.0, occurred_at="2026-02-01T00:00:00")
        check("a deal with no cards on either side is refused", False, "no error raised")
    except ValueError:
        check("a deal with no cards on either side is refused", True)


# ── schema-7 regressions (typed vs derived basis, 2026-09-25) ──────────────────

def test_blank_market_stays_blank(c):
    """A take-in with no market typed must stay NULL forever — never copied from
    the take-in price the way the old engine did."""
    save_deal(c, cards_in=[CardIn(card_name="NoMarket", deal_value=500.0, market_value=None)],
              cash_amount=-500.0, occurred_at="2026-09-25T00:00:00")
    row = c.execute("SELECT id, market_value FROM graded_cards WHERE card_name='NoMarket'").fetchone()
    check("blank market stays NULL on the new card", row["market_value"] is None,
          f"market_value={row['market_value']}")
    n = c.execute("SELECT COUNT(*) FROM price_history WHERE card_id=?", (row["id"],)).fetchone()[0]
    check("no price_history row is seeded from a blank market", n == 0, f"found {n} row(s)")


def test_typed_market_seeds_price_history_as_entered(c):
    """A typed market on an incoming card becomes its first price point,
    tagged source='entered' — never 'copied' from the take-in."""
    save_deal(c, cards_in=[CardIn(card_name="WithMarket", deal_value=500.0, market_value=700.0)],
              cash_amount=-500.0, occurred_at="2026-09-25T00:00:00")
    cid = c.execute("SELECT id FROM graded_cards WHERE card_name='WithMarket'").fetchone()[0]
    ph = c.execute("SELECT market_value, source FROM price_history WHERE card_id=?", (cid,)).fetchone()
    check("typed market seeds price_history as 'entered'",
          ph is not None and ph["market_value"] == 700.0 and ph["source"] == "entered",
          f"{dict(ph) if ph else None}")


def test_typed_take_in_lands_in_basis_entered(c):
    be, bd = basis_cols(c, "graded_cards", save_deal(
        c, cards_in=[CardIn(card_name="TypedIn", deal_value=500.0)],
        cash_amount=-500.0, occurred_at="2026-09-25T00:00:00")["in_lines"][0]["card_id"])
    check("typed take-in lands in basis_entered", be == 500.0, f"basis_entered={be}")
    check("basis_derived stays NULL when the take-in was typed", bd is None, f"basis_derived={bd}")


def test_blank_take_in_lands_in_basis_derived(c):
    a = slab(c, "AskedOut", 1000, 1200)
    r = save_deal(c, cards_out=[CardOut("graded_cards", a, None)], out_side_total=1000.0,
                  cards_in=[CardIn(card_name="BlankIn", deal_value=None, market_value=500.0)],
                  cash_amount=0.0, occurred_at="2026-09-25T00:00:00")
    be, bd = basis_cols(c, "graded_cards", r["in_lines"][0]["card_id"])
    check("blank take-in lands in basis_derived", bd == 1000.0, f"basis_derived={bd}")
    check("basis_entered stays NULL when the take-in was blank", be is None, f"basis_entered={be}")


def test_blank_ask_with_blank_take_in_refused(c):
    """Cards go out, nothing is typed anywhere on the ask side, and one take-in
    is blank: there is nothing to split it from, so the engine refuses rather
    than silently defaulting to market."""
    a = slab(c, "NoAsk", 500, 600)
    before = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    try:
        save_deal(c, cards_out=[CardOut("graded_cards", a, None)],
                  cards_in=[CardIn(card_name="BlankBlank", deal_value=None)],
                  cash_amount=0.0, occurred_at="2026-09-25T00:00:00")
        check("blank ask with a blank take-in is refused", False, "no error raised")
    except ValueError:
        after = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        check("blank ask with a blank take-in is refused", after == before,
              f"deals count {before} -> {after}")


def test_cash_paid_over_take_in_refused(c):
    a = slab(c, "CashOver", 500, 600)
    before = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    try:
        save_deal(c, cards_out=[CardOut("graded_cards", a, 500.0)],
                  cards_in=[CardIn(card_name="SmallIn", deal_value=100.0)],
                  cash_amount=-1000.0, occurred_at="2026-09-25T00:00:00")
        check("cash paid over the take-in is refused", False, "no error raised")
    except ValueError:
        after = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        check("cash paid over the take-in is refused", after == before,
              f"deals count {before} -> {after}")


def test_nameless_arrival_refused(c):
    before = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    try:
        save_deal(c, cards_in=[CardIn(card_name="   ", deal_value=100.0)],
                  cash_amount=-100.0, occurred_at="2026-09-25T00:00:00")
        check("an arriving card with an empty name is refused", False, "no error raised")
    except ValueError:
        after = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        check("an arriving card with an empty name is refused", after == before,
              f"deals count {before} -> {after}")


def test_nothing_came_back_refused(c):
    a = slab(c, "Stranded", 500, 600)
    before = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    try:
        save_deal(c, cards_out=[CardOut("graded_cards", a, None)],
                  cash_amount=0.0, occurred_at="2026-09-25T00:00:00")
        check("nothing came back (no cash, no cards in) is refused", False, "no error raised")
    except ValueError:
        after = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        check("nothing came back (no cash, no cards in) is refused", after == before,
              f"deals count {before} -> {after}")


def test_deal_lines_written(c):
    a = slab(c, "LineOut", 800, 1000)
    r = save_deal(c, cards_out=[CardOut("graded_cards", a, 900.0)],
                  cards_in=[CardIn(card_name="LineIn", deal_value=400.0, market_value=450.0)],
                  cash_amount=500.0, occurred_at="2026-09-25T00:00:00")
    lines = c.execute("SELECT * FROM deal_lines WHERE deal_id=?", (r["deal_id"],)).fetchall()
    check("one deal_lines row per line", len(lines) == 2, f"got {len(lines)}")
    out_line = next((l for l in lines if l["side"] == "out"), None)
    in_line = next((l for l in lines if l["side"] == "in"), None)
    check("out line entered_value equals what was typed",
          out_line is not None and out_line["entered_value"] == 900.0)
    check("out line derived_value equals the booked proceeds",
          out_line is not None and abs(out_line["derived_value"] - proceeds_of(c, a)) < 0.01)
    check("out line basis_at_deal equals the card's basis before the deal",
          out_line is not None and out_line["basis_at_deal"] == 800.0)
    check("out line market_at_deal equals the card's market before the deal",
          out_line is not None and out_line["market_at_deal"] == 1000.0)
    check("in line entered_value equals what was typed",
          in_line is not None and in_line["entered_value"] == 400.0)
    got_basis = c.execute("SELECT basis_entered FROM graded_cards WHERE card_name='LineIn'").fetchone()[0]
    check("in line derived_value equals the booked basis",
          in_line is not None and abs(in_line["derived_value"] - got_basis) < 0.01)


def test_entry_payload_logged_in_same_transaction(c):
    payload = {"cards_out": [], "cards_in": [{"name": "PayloadIn", "deal_value": 250.0}],
               "cash_amount": -250.0}
    r = save_deal(c, cards_in=[CardIn(card_name="PayloadIn", deal_value=250.0)],
                  cash_amount=-250.0, occurred_at="2026-09-25T00:00:00", entry_payload=payload)
    row = c.execute("SELECT payload_json FROM deal_entries WHERE deal_id=?", (r["deal_id"],)).fetchone()
    check("entry_payload is logged in the same transaction", row is not None, "no deal_entries row")
    if row is not None:
        check("logged payload matches what was passed", json.loads(row["payload_json"]) == payload,
              f"{row['payload_json']}")


def test_void_archives_then_restores(c):
    a = slab(c, "VoidOut", 800, 1000)
    before_a = dict(c.execute("SELECT * FROM graded_cards WHERE id=?", (a,)).fetchone())
    r = save_deal(c, cards_out=[CardOut("graded_cards", a, 900.0)],
                  cards_in=[CardIn(card_name="VoidIn", deal_value=400.0)],
                  cash_amount=500.0, occurred_at="2026-09-25T00:00:00")
    deal_id = r["deal_id"]
    in_id = r["in_lines"][0]["card_id"]
    void_deal(c, deal_id)
    voided = c.execute("SELECT * FROM voided_deals WHERE deal_id=?", (deal_id,)).fetchall()
    check("voided_deals has exactly one row", len(voided) == 1, f"got {len(voided)}")
    check("the archived snapshot contains the deal",
          bool(voided) and str(deal_id) in voided[0]["snapshot_json"], "deal id not found in snapshot")
    after_a = dict(c.execute("SELECT * FROM graded_cards WHERE id=?", (a,)).fetchone())
    check("the out card's row equals its pre-deal state", before_a == after_a,
          f"before={before_a}\nafter={after_a}")
    gone = c.execute("SELECT COUNT(*) FROM graded_cards WHERE id=?", (in_id,)).fetchone()[0]
    check("the in card the deal created is removed", gone == 0, f"count={gone}")


def test_generated_columns_refuse_writes(c):
    try:
        c.execute("UPDATE graded_cards SET acquisition_price=1")
        check("UPDATE of a generated column raises OperationalError", False, "no error raised")
    except sqlite3.OperationalError:
        check("UPDATE of a generated column raises OperationalError", True)


# ── edit / void / refusal regressions (work orders E1-E11, 2026-09-25) ──────────

WHEN = "2026-09-20T12:00:00"


def buy_giratina(c, market=3500.0, paid=3000.0):
    """A pure buy of one slab with a typed market. Returns (deal_id, card_id)."""
    r = save_deal(c, cards_in=[CardIn(card_name="Giratina", set_name="Lost Origin",
                                      card_number="186", grading_company="PSA", grade="10",
                                      deal_value=paid, market_value=market)],
                  cash_amount=-paid, occurred_at=WHEN)
    return r["deal_id"], r["in_lines"][0]["card_id"]


def giratina_line(market=3500.0, paid=3000.0, **kw):
    spec = dict(card_name="Giratina", set_name="Lost Origin", card_number="186",
                grading_company="PSA", grade="10", deal_value=paid, market_value=market)
    spec.update(kw)
    return CardIn(**spec)


def card(c, cid, table="graded_cards"):
    return c.execute(f"SELECT * FROM {table} WHERE id=?", (cid,)).fetchone()


def points(c, cid):
    return sorted((r["recorded_at"], r["market_value"]) for r in c.execute(
        "SELECT recorded_at, market_value FROM price_history WHERE card_id=?", (cid,)))


def test_edit_keeps_later_reprice_cert_and_rename(c):
    """E1: Edit deal rewrote a kept card's identity and market from the form,
    wiping a reprice, a cert and a rename made after the deal was booked."""
    did, cid = buy_giratina(c)
    v2cards.reprice(c, cid, 3400.0)
    v2db.update_card(c, "graded_cards", cid,
                     {"card_name": "Giratina V", "serial_number": "81234567"}, "you", "drawer")
    c.commit()
    # same typing for the card; the take-in and cash change
    edit_deal(c, did, cards_in=[giratina_line(paid=2900.0)],
              in_card_refs=[("graded_cards", cid)], cash_amount=-2900.0,
              occurred_at=WHEN, counterparty="table 12")
    row = card(c, cid)
    check("edit keeps a later rename", row["card_name"] == "Giratina V", row["card_name"])
    check("edit keeps a later cert", row["serial_number"] == "81234567", row["serial_number"])
    check("edit keeps a later reprice", row["market_value"] == 3400.0, f"{row['market_value']}")
    check("edit still rewrites the basis", row["basis_entered"] == 2900.0,
          f"{row['basis_entered']}")
    check("the reprice's price point survives",
          any(mv == 3400.0 for _, mv in points(c, cid)), f"{points(c, cid)}")
    check("the deal-seeded point is untouched when the typed market did not change",
          (WHEN, 3500.0) in points(c, cid), f"{points(c, cid)}")
    # a changed typed name is written
    edit_deal(c, did, cards_in=[giratina_line(paid=2900.0, card_name="Giratina VSTAR")],
              in_card_refs=[("graded_cards", cid)], cash_amount=-2900.0, occurred_at=WHEN)
    check("a name retyped in the edit is written", card(c, cid)["card_name"] == "Giratina VSTAR",
          card(c, cid)["card_name"])
    check("the cert still survives", card(c, cid)["serial_number"] == "81234567")
    r = replay(c, did)
    check("the edited deal replays exactly", r["ok"], f"{r}")


def test_edit_market_change_replaces_only_the_seeded_point(c):
    """E1: _unbook deleted every price point at the deal's second, including a
    reprice that shared it. Only the deal-seeded point goes, and only when
    the typed market changed."""
    did, cid = buy_giratina(c)
    # a reprice in the very same second as the deal
    c.execute("INSERT INTO price_history (card_id, recorded_at, market_value, source) "
              "VALUES (?,?,3600,'entered')", (cid, WHEN))
    c.commit()
    edit_deal(c, did, cards_in=[giratina_line()], in_card_refs=[("graded_cards", cid)],
              cash_amount=-3000.0, occurred_at=WHEN)
    check("unchanged typed market leaves every price point alone",
          points(c, cid) == [(WHEN, 3500.0), (WHEN, 3600.0)], f"{points(c, cid)}")
    edit_deal(c, did, cards_in=[giratina_line(market=3700.0)],
              in_card_refs=[("graded_cards", cid)], cash_amount=-3000.0, occurred_at=WHEN)
    check("changed typed market replaces only the deal-seeded point",
          points(c, cid) == [(WHEN, 3600.0), (WHEN, 3700.0)], f"{points(c, cid)}")
    check("changed typed market is written to the card", card(c, cid)["market_value"] == 3700.0)
    edit_deal(c, did, cards_in=[giratina_line(market=None)],
              in_card_refs=[("graded_cards", cid)], cash_amount=-3000.0, occurred_at=WHEN)
    check("blanking the typed market removes its point and seeds none",
          points(c, cid) == [(WHEN, 3600.0)], f"{points(c, cid)}")
    check("blanked market is blank on the card", card(c, cid)["market_value"] is None)


def test_legacy_deal_edit_refused(c):
    """E2: a deal from before the entry log has nothing typed to edit."""
    did, cid = buy_giratina(c)
    check("a deal with an entry log is editable", get_deal(c, did)["editable"] is True)
    c.execute("DELETE FROM deal_entries WHERE deal_id=?", (did,))
    c.commit()
    check("a deal without an entry log is not editable", get_deal(c, did)["editable"] is False)
    refused(c, "editing a deal from before the entry log is refused",
            lambda: edit_deal(c, did, cards_in=[giratina_line()],
                              in_card_refs=[("graded_cards", cid)], cash_amount=-3000.0),
            f"Deal #{did} was booked before the entry log")
    did2, _ = buy_giratina(c)
    c.execute("DELETE FROM deal_lines WHERE deal_id=?", (did2,))
    c.commit()
    check("a deal without deal_lines is not editable", get_deal(c, did2)["editable"] is False)
    check("the card survives the refused edit", card(c, cid) is not None)


def test_edit_slab_raw_switch_refused(c):
    """E3: a kept card can't switch between slab and raw in an edit."""
    did, cid = buy_giratina(c)
    refused(c, "switching a kept card from slab to raw is refused",
            lambda: edit_deal(c, did, cards_in=[giratina_line(is_graded=False)],
                              in_card_refs=[("graded_cards", cid)], cash_amount=-3000.0),
            "between slab and raw")
    check("the slab is untouched", card(c, cid)["status"] == "active")
    check("the deal is untouched",
          c.execute("SELECT COUNT(*) FROM voided_deals").fetchone()[0] == 0)


def test_edit_duplicate_refs_refused(c):
    """E3: two lines can't point at the same kept card."""
    did, cid = buy_giratina(c)
    refused(c, "two lines on the same kept card are refused",
            lambda: edit_deal(c, did, cards_in=[giratina_line(paid=1500.0),
                                                giratina_line(paid=1500.0)],
                              in_card_refs=[("graded_cards", cid), ("graded_cards", cid)],
                              cash_amount=-3000.0),
            "same card")


def test_later_changes_lists_a_reprice(c):
    """E4: later_changes names what an undo would destroy, once per change."""
    did, cid = buy_giratina(c)
    check("a fresh deal has no later changes", later_changes(c, did) == [],
          f"{later_changes(c, did)}")
    v2cards.reprice(c, cid, 3400.0)
    ch = later_changes(c, did)
    check("later_changes lists the reprice in plain English",
          len(ch) == 1 and ch[0].startswith("Giratina: market 3500 → 3400 (reprice, "),
          f"{ch}")


def test_void_refused_without_confirm(c):
    """E4: a void must not silently discard later work on arriving cards."""
    did, cid = buy_giratina(c)
    v2cards.reprice(c, cid, 3400.0)
    e = refused(c, "void of a deal with later changes is refused without confirm",
                lambda: void_deal(c, did), "reprice", exc=LaterChangesError)
    check("the refusal carries the changes", e is not None and len(e.changes) == 1,
          f"{getattr(e, 'changes', None)}")
    check("the card survives the refused void", card(c, cid) is not None)
    res = void_deal(c, did, confirm_lose_changes=True)
    check("confirmed void goes through", card(c, cid) is None
          and c.execute("SELECT COUNT(*) FROM deals WHERE id=?", (did,)).fetchone()[0] == 0)
    check("confirmed void reports what was lost", len(res["lost_changes"]) == 1,
          f"{res['lost_changes']}")


def test_edit_dropping_a_changed_card_needs_confirm(c):
    """E4: an edit that drops an arriving line deletes the card, so its later
    changes need the same confirmation; kept cards never count."""
    r = save_deal(c, cards_in=[giratina_line(), CardIn(card_name="Arceus", deal_value=500.0)],
                  cash_amount=-3500.0, occurred_at=WHEN)
    did = r["deal_id"]
    gid, aid = r["in_lines"][0]["card_id"], r["in_lines"][1]["card_id"]
    v2cards.reprice(c, gid, 3400.0)
    v2cards.reprice(c, aid, 650.0)
    only_giratina = dict(cards_in=[giratina_line()], in_card_refs=[("graded_cards", gid)],
                         cash_amount=-3000.0, occurred_at=WHEN)
    e = refused(c, "edit dropping a repriced card is refused without confirm",
                lambda: edit_deal(c, did, **only_giratina), "Arceus", exc=LaterChangesError)
    check("only the dropped card's changes are listed",
          e is not None and all(x.startswith("Arceus") for x in e.changes), f"{e and e.changes}")
    prev = edit_deal(c, did, dry_run=True, **only_giratina)
    check("the edit preview lists the loss instead of refusing",
          prev["later_changes"] and any("Arceus" in w for w in prev["warnings"]), f"{prev}")
    edit_deal(c, did, confirm_lose_changes=True, **only_giratina)
    check("confirmed edit drops the card", card(c, aid) is None)
    check("the kept card keeps its reprice", card(c, gid)["market_value"] == 3400.0)


def test_pure_buy_take_ins_must_match_cash(c):
    """E5: on a pure buy the cards cost exactly the cash paid."""
    refused(c, "pure buy with typed take-ins short of the cash is refused",
            lambda: save_deal(c, cards_in=[CardIn(card_name="P1", deal_value=100.0),
                                           CardIn(card_name="P2", deal_value=50.0)],
                              cash_amount=-200.0, occurred_at=WHEN),
            "You paid $200.00 but the cards' prices add up to $150.00")
    refused(c, "pure buy with typed take-ins over the cash is refused",
            lambda: save_deal(c, cards_in=[CardIn(card_name="P3", deal_value=250.0)],
                              cash_amount=-200.0, occurred_at=WHEN),
            "prices add up to $250.00")
    r = save_deal(c, cards_in=[CardIn(card_name="M1", deal_value=120.0),
                               CardIn(card_name="M2", deal_value=80.0)],
                  cash_amount=-200.0, occurred_at=WHEN)
    check("pure buy whose take-ins match the cash books", bool(r["deal_id"]))
    r = save_deal(c, cards_in=[CardIn(card_name="Q1", deal_value=150.0),
                               CardIn(card_name="Q2", deal_value=None)],
                  cash_amount=-200.0, occurred_at=WHEN)
    check("partly typed pure buy splits the rest to the blank",
          abs(in_basis_of(c, "Q2") - 50.0) < 0.005, f"{in_basis_of(c, 'Q2')}")
    refused(c, "partly typed pure buy whose typed take-ins exceed the cash is refused",
            lambda: save_deal(c, cards_in=[CardIn(card_name="R1", deal_value=250.0),
                                           CardIn(card_name="R2", deal_value=None)],
                              cash_amount=-200.0, occurred_at=WHEN),
            "You paid $200.00")


def test_partial_split_values_refused(c):
    """E6: per-card split values go on every outgoing card or on none."""
    a, b = slab(c, "S1", 100, 300), slab(c, "S2", 100, 300)
    refused(c, "split typed on some outgoing cards but not all is refused",
            lambda: save_deal(c, cards_out=[CardOut("graded_cards", a, 300.0),
                                            CardOut("graded_cards", b, None)],
                              cards_in=[CardIn(card_name="T1", deal_value=500.0)],
                              occurred_at=WHEN),
            "Type a split value on every card going out, or on none.")


def test_nothing_came_back(c):
    """E7: no cash and no cards in books only as an all-zero giveaway."""
    a, b = slab(c, "G1", 100, 300), slab(c, "G2", 100, 300)
    refused(c, "nothing came back with a positive typed value is refused",
            lambda: save_deal(c, cards_out=[CardOut("graded_cards", a, 0.0),
                                            CardOut("graded_cards", b, 50.0)],
                              occurred_at=WHEN),
            "Nothing came back, so these cards can't book proceeds.")
    r = save_deal(c, cards_out=[CardOut("graded_cards", a, 0.0),
                                CardOut("graded_cards", b, 0.0)], occurred_at=WHEN)
    check("an all-zero giveaway books at $0 with no warning",
          proceeds_of(c, a) == 0.0 and proceeds_of(c, b) == 0.0 and not r["warnings"],
          f"{proceeds_of(c, a)} / {proceeds_of(c, b)} / {r['warnings']}")


def test_no_clamp_global(c):
    """E8: the clamp amount is returned, not left in a module global that
    threaded Flask requests could read from each other."""
    check("no module-level _LAST_CLAMP", not hasattr(v2deals, "_LAST_CLAMP"))
    vals, _, short = v2deals._resolve_side([300.0, None], [1.0, 1.0], 200.0)
    check("_resolve_side returns the shortfall", short == 100.0 and vals == [300.0, 0.0],
          f"{vals} {short}")


def test_allocate_many_lines_exact_and_non_negative(c):
    """E9: $1 over 200 lines. The old rounding put every line at $0.01 and
    dumped -$1.00 on the last one."""
    vals = _allocate(1.0, [1.0] * 200)
    cents = [round(v * 100) for v in vals]
    check("$1 over 200 lines never goes negative", min(vals) >= 0, f"min {min(vals)}")
    check("$1 over 200 lines sums exactly", sum(cents) == 100, f"sum {sum(cents)} cents")
    check("each share is within a cent of exact", all(abs(v - 0.005) <= 0.01 for v in vals))
    w = [1, 2, 3, 1000, 0.5, 7]
    vals = _allocate(123.45, w)
    exact = [123.45 * x / sum(w) for x in w]
    check("weighted shares sum exactly and stay within a cent",
          sum(round(v * 100) for v in vals) == 12345
          and all(abs(v - e) < 0.01 + 1e-9 for v, e in zip(vals, exact)), f"{vals}")


def test_bad_occurred_at_refused(c):
    """E10: a deal date is ISO or refused, and stored to the second."""
    for bad in ("25/09/2026", "2026-09-25 12:00", "yesterday", "2026-9-5", "2026-09-25T12"):
        refused(c, f"deal date {bad!r} is refused",
                lambda bad=bad: save_deal(c, cards_in=[CardIn(card_name="D", deal_value=10.0)],
                                          cash_amount=-10.0, occurred_at=bad),
                "deal date")
    for typed, stored in (("2026-09-25", "2026-09-25T00:00:00"),
                          ("2026-09-25T14:30", "2026-09-25T14:30:00"),
                          ("2026-09-25T14:30:15", "2026-09-25T14:30:15")):
        r = save_deal(c, cards_in=[CardIn(card_name="D", deal_value=10.0)],
                      cash_amount=-10.0, occurred_at=typed)
        got = c.execute("SELECT occurred_at FROM deals WHERE id=?", (r["deal_id"],)).fetchone()[0]
        check(f"deal date {typed!r} stored as {stored!r}", got == stored, got)
    did, cid = buy_giratina(c)
    refused(c, "edit with a bad deal date is refused",
            lambda: edit_deal(c, did, cards_in=[giratina_line()],
                              in_card_refs=[("graded_cards", cid)], cash_amount=-3000.0,
                              occurred_at="Sept 20"),
            "deal date")


def test_replay_detects_cash_tampering(c):
    """E11: replay compares the stored cash with what was typed."""
    a = slab(c, "Cashy", 100, 300)
    r = save_deal(c, cards_out=[CardOut("graded_cards", a, None)],
                  cards_in=[CardIn(card_name="Back", deal_value=100.0)],
                  cash_amount=200.0, occurred_at=WHEN)
    check("an untouched deal replays", replay(c, r["deal_id"])["ok"], f"{replay(c, r['deal_id'])}")
    c.execute("UPDATE deals SET cash_amount=250 WHERE id=?", (r["deal_id"],))
    c.commit()
    rep = replay(c, r["deal_id"])
    check("replay reports a tampered cash amount",
          not rep["ok"] and any(d.startswith("cash:") for d in rep["diffs"]), f"{rep}")


TESTS = [test_pure_sale_scales_to_cash, test_pure_sale_scales_with_an_unvalued_line,
         test_zero_cash_is_not_scaled,
         test_unknown_basis_books_no_gain, test_cash_received_reduces_basis_flowing_in,
         test_agreed_price_beats_market_everywhere,
         test_blank_in_lines_split_pro_rata_by_market, test_deal_37_books_under_stefans_model,
         test_worked_example_umbreon, test_two_cards_out_split_by_typed_values,
         test_double_disposal_refused, test_allocation_is_penny_exact, test_empty_deal_refused,
         test_blank_market_stays_blank, test_typed_market_seeds_price_history_as_entered,
         test_typed_take_in_lands_in_basis_entered, test_blank_take_in_lands_in_basis_derived,
         test_blank_ask_with_blank_take_in_refused, test_cash_paid_over_take_in_refused,
         test_nameless_arrival_refused, test_nothing_came_back_refused,
         test_deal_lines_written, test_entry_payload_logged_in_same_transaction,
         test_void_archives_then_restores, test_generated_columns_refuse_writes,
         test_edit_keeps_later_reprice_cert_and_rename,
         test_edit_market_change_replaces_only_the_seeded_point,
         test_legacy_deal_edit_refused, test_edit_slab_raw_switch_refused,
         test_edit_duplicate_refs_refused, test_later_changes_lists_a_reprice,
         test_void_refused_without_confirm, test_edit_dropping_a_changed_card_needs_confirm,
         test_pure_buy_take_ins_must_match_cash, test_partial_split_values_refused,
         test_nothing_came_back, test_no_clamp_global,
         test_allocate_many_lines_exact_and_non_negative, test_bad_occurred_at_refused,
         test_replay_detects_cash_tampering]


def main():
    print("deal engine regressions")
    for t in TESTS:
        c = fresh_db()          # each test gets a clean database
        try:
            t(c)
        except Exception as e:
            FAILURES.append(t.__name__)
            print(f"  ERROR  {t.__name__}: {type(e).__name__}: {e}")
        finally:
            c.close()
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(f"   failed: {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
