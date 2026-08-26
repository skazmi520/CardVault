"""
CardVault v2 — deal engine regression tests.

Unlike test_deals.py (which prints a worked example for eyeballing), every test
here ASSERTS and fails loudly. Each one pins down a rule that was broken at some
point, so the same bug cannot come back quietly.

Runs against a throwaway database cloned from the live schema. The real database
is opened read-only for its DDL and never written.

Run:  python3 -m v2.test_engine
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

from . import db as v2db
from .deals import CardOut, CardIn, save_deal

FAILURES = []
PASSES = []


def check(name, cond, detail=""):
    (PASSES if cond else FAILURES).append(name)
    print(f"  {'ok    ' if cond else 'FAIL  '} {name}" + (f"\n           {detail}" if not cond and detail else ""))


def fresh_db():
    src = sqlite3.connect(f"file:{v2db.V2_DB_PATH}?mode=ro", uri=True)
    ddl = [r[0] for r in src.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")]
    src.close()
    path = Path(tempfile.mkdtemp(prefix="cv_engine_")) / "t.db"
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    for s in ddl:
        c.execute(s)
    c.execute("INSERT OR REPLACE INTO v2_meta (key,value) VALUES ('is_v2','1')")
    c.commit()
    return c


def slab(c, name, basis, mv, unknown=0):
    cur = c.execute(
        "INSERT INTO graded_cards (card_name, grading_company, grade, acquisition_price, "
        "acquisition_date, market_value, basis_unknown, status, date_added, notes, set_name, card_number) "
        "VALUES (?,'PSA','10',?,'2026-01-01',?,?,'active','2026-01-01','','','')",
        (name, basis, mv, unknown))
    c.commit()
    return cur.lastrowid


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


TESTS = [test_pure_sale_scales_to_cash, test_pure_sale_scales_with_an_unvalued_line,
         test_zero_cash_is_not_scaled,
         test_unknown_basis_books_no_gain, test_cash_received_reduces_basis_flowing_in,
         test_double_disposal_refused, test_allocation_is_penny_exact, test_empty_deal_refused]


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
