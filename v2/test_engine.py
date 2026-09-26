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

from . import db as v2db
from .deals import CardOut, CardIn, save_deal, void_deal

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
    kept as-is — booking $6,467 of proceeds against $5,405 received."""
    a, b = slab(c, "A1", 800, 1000), slab(c, "B1", 800, 1000)
    save_deal(c, cards_out=[CardOut("graded_cards", a, 4000.0),
                            CardOut("graded_cards", b, None)],      # <- blank
              cash_amount=1500.0, occurred_at="2026-02-01T00:00:00")
    tot = proceeds_of(c, a) + proceeds_of(c, b)
    check("pure sale scales even when a line has no value entered",
          abs(tot - 1500.0) < 0.01, f"proceeds {tot} should be 1500")


def test_zero_cash_is_not_scaled(c):
    """Zero cash is ambiguous — giveaway, or a sale whose cash was not logged.
    Auto-zeroing would have destroyed $X of real gains on the [unreconciled]
    deals, so the engine must warn and leave the entered values alone."""
    a = slab(c, "C", 500, 900)
    r = save_deal(c, cards_out=[CardOut("graded_cards", a, 900.0)],
                  cash_amount=0.0, occurred_at="2026-02-01T00:00:00")
    check("zero-cash sale keeps entered values", abs(proceeds_of(c, a) - 900.0) < 0.01,
          f"proceeds {proceeds_of(c, a)} should stay 900")
    check("zero-cash sale warns", any("no cash" in w for w in r["warnings"]),
          f"warnings: {r['warnings']}")


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
         test_void_archives_then_restores, test_generated_columns_refuse_writes]


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
