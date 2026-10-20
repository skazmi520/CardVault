"""
CardVault v2: card operation regression tests (v2/cards.py, PSA order import).

Same shape as test_engine.py: every test ASSERTS, and each pins a rule that
was broken or decided at some point. Runs against throwaway databases built
from the schema v2/db.py defines (v2db.scratch_database). The live database
is never opened.

Run:  python3 -m v2.test_cards
"""

import sys

from . import cards as v2cards
from . import db as v2db
from . import psa_orders

FAILURES = []
PASSES = []


def check(name, cond, detail=""):
    (PASSES if cond else FAILURES).append(name)
    print(f"  {'ok    ' if cond else 'FAIL  '} {name}" + (f"\n           {detail}" if not cond and detail else ""))


def fresh_db():
    return v2db.scratch_database(prefix="cv_cards_")


def slab(c, name, basis, mv=None, is_pc=False):
    return v2cards.create_card(c, "graded_cards", card_name=name, basis=basis,
                               market_value=mv, grade="10", is_pc=is_pc)


def raw(c, name, basis, mv=None, is_pc=False):
    return v2cards.create_card(c, "ungraded_cards", card_name=name, basis=basis,
                               market_value=mv, is_pc=is_pc)


def row(c, table, cid):
    return c.execute(f"SELECT * FROM {table} WHERE id=?", (cid,)).fetchone()


def events(c, table, cid, field=None):
    q = "SELECT COUNT(*) FROM card_events WHERE card_table=? AND card_id=?"
    args = [table, cid]
    if field:
        q += " AND field=?"
        args.append(field)
    return c.execute(q, args).fetchone()[0]


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except ValueError:
        return True
    return False


# ── dashboard ──────────────────────────────────────────────────────────────────

def test_dashboard_keeps_pc_on_its_own_line(c):
    """Decided 2026-09-25: stock figures cover business inventory only; the
    personal collection is reported beside them, never mixed in."""
    slab(c, "Priced", 100, 150)
    slab(c, "No market", 200)                    # counts at cost, flagged at_cost
    raw(c, "Raw no market", 30)                  # raw at cost, not a slab
    slab(c, "PC priced", 50, 400, is_pc=True)
    slab(c, "PC no market", 10, is_pc=True)      # PC: not an at-cost slab
    gone = slab(c, "Lost", 999)
    v2cards.mark_lost(c, "graded_cards", gone)
    s = v2cards.dashboard_stats(c)
    check("dashboard market excludes PC", s["market"] == 380.0, f"market {s['market']} != 380")
    check("dashboard basis excludes PC", s["basis"] == 330.0, f"basis {s['basis']} != 330")
    check("dashboard unrealized excludes PC", s["unrealized"] == 50.0,
          f"unrealized {s['unrealized']} != 50")
    check("dashboard reports pc_value and pc_count",
          s["pc_value"] == 410.0 and s["pc_count"] == 2, f"{s['pc_value']} / {s['pc_count']}")
    check("dashboard holding = market + pc_value", s["holding"] == 790.0,
          f"holding {s['holding']} != 790")
    check("slabs_at_cost counts active non-PC slabs with no market only",
          s["slabs_at_cost"] == 1 and s["slabs_at_cost_value"] == 200.0,
          f"{s['slabs_at_cost']} / {s['slabs_at_cost_value']}")
    check("cards_active excludes PC and lost", s["cards_active"] == 3, str(s["cards_active"]))


def test_snapshot_records_holding(c):
    slab(c, "A", 100, 150)
    slab(c, "PC", 50, 400, is_pc=True)
    v2cards.record_snapshot(c)
    v2cards.record_snapshot(c)                   # same day: upsert, not a second row
    snaps = c.execute("SELECT total_value, card_count FROM portfolio_snapshots").fetchall()
    check("snapshot is one row per day", len(snaps) == 1, f"{len(snaps)} rows")
    check("snapshot stores holding (stock + PC)",
          snaps[0]["total_value"] == 550.0 and snaps[0]["card_count"] == 2,
          f"{dict(snaps[0])}")


def test_top_movers_ignore_copied_points(c):
    """A take-in the old engine copied into price_history was never a market,
    so it must not show up as a price move."""
    a, b = slab(c, "Copied", 100, 100), slab(c, "Moved", 100, 100)
    c.executemany("INSERT INTO price_history (card_id, recorded_at, market_value, source) "
                  "VALUES (?,?,?,?)",
                  [(a, "2026-01-01T00:00:00", 100, "entered"),
                   (a, "2026-02-01T00:00:00", 500, "copied"),
                   (b, "2026-01-01T00:00:00", 100, "entered"),
                   (b, "2026-02-01T00:00:00", 120, "entered")])
    c.commit()
    names = {m["name"]: m for m in v2cards.top_movers(c)}
    check("top movers skip a copied price point", "Copied" not in names, str(list(names)))
    check("top movers keep real moves", names.get("Moved", {}).get("delta") == 20.0,
          str(names.get("Moved")))


# ── repricing ──────────────────────────────────────────────────────────────────

def test_reprice_and_confirm_refuse_cards_not_held(c):
    """A card that left keeps the market it had; the deal page reads it as the
    market at the time."""
    lost = slab(c, "Lost", 100, 150)
    v2cards.mark_lost(c, "graded_cards", lost)
    sold = raw(c, "Sold", 40, 60)
    v2db.update_card(c, "ungraded_cards", sold, {"status": "disposed",
                                                 "disposal_proceeds": 60.0}, "engine")
    c.commit()
    check("reprice refuses a lost card", raises(v2cards.reprice, c, lost, 200.0))
    check("confirm_price refuses a lost card", raises(v2cards.confirm_price, c, lost))
    check("reprice refuses a disposed card",
          raises(v2cards.reprice, c, sold, 80.0, table="ungraded_cards"))
    check("confirm_price refuses a disposed card",
          raises(v2cards.confirm_price, c, sold, table="ungraded_cards"))
    check("refused reprice leaves market untouched",
          row(c, "graded_cards", lost)["market_value"] == 150.0)

    held = slab(c, "Held", 100, 150)
    v2cards.reprice(c, held, 175.0)
    check("reprice writes card_events", events(c, "graded_cards", held, "market_value") == 1)
    # backdate the stamp so confirming today is a real change to log
    v2db.update_card(c, "graded_cards", held, {"market_value_updated": "2026-01-01"}, "you")
    n = events(c, "graded_cards", held, "market_value_updated")
    v2cards.confirm_price(c, held)
    check("confirm_price writes card_events",
          events(c, "graded_cards", held, "market_value_updated") == n + 1)


# ── creation ───────────────────────────────────────────────────────────────────

def test_create_card_blank_basis_and_market_stay_null(c):
    """Blank is not $0: an unknown basis stays out of every gain statistic,
    and nothing fills a blank market."""
    for table in ("graded_cards", "ungraded_cards"):
        cid = v2cards.create_card(c, table, card_name="Mystery", basis="", market_value="")
        r = row(c, table, cid)
        basis_col = "acquisition_price" if table == "graded_cards" else "purchase_price"
        check(f"create_card blank basis stores NULL ({table})",
              r["basis_entered"] is None and r[basis_col] is None and r["basis_unknown"] == 1,
              f"{r['basis_entered']} / {r[basis_col]} / {r['basis_unknown']}")
        check(f"create_card blank market stays NULL ({table})",
              r["market_value"] is None and r["market_value_updated"] is None)
        check(f"create_card writes a card_events row ({table})",
              events(c, table, cid, "created") == 1)
    cid = v2cards.create_card(c, "graded_cards", card_name="Typed", basis="0")
    r = row(c, "graded_cards", cid)
    check("create_card typed $0 is a known basis, not unknown",
          r["basis_entered"] == 0.0 and r["basis_unknown"] == 0)


# ── grading lifecycle ──────────────────────────────────────────────────────────

def test_promote_raw_unknown_basis_stays_unknown(c):
    rid = raw(c, "Unknown raw", None)
    gid = v2cards.promote_raw(c, rid, grading_company="PSA", grade="9",
                              serial_number="1001", grading_cost=25)
    g = row(c, "graded_cards", gid)
    check("promote_raw: unknown raw basis gives unknown slab basis",
          g["basis_derived"] is None and g["basis_unknown"] == 1 and g["grading_fee"] == 25.0,
          f"{g['basis_derived']} / {g['basis_unknown']} / {g['grading_fee']}")
    check("promote_raw writes card_events on both rows",
          events(c, "graded_cards", gid, "created") == 1
          and events(c, "ungraded_cards", rid, "status") == 1)

    rid = raw(c, "Known raw", 100)
    gid = v2cards.promote_raw(c, rid, grading_company="PSA", grade="10",
                              serial_number="1002", grading_cost=20)
    g = row(c, "graded_cards", gid)
    check("promote_raw: basis is derived raw cost + fee, never typed",
          g["basis_derived"] == 120.0 and g["basis_entered"] is None
          and g["acquisition_price"] == 120.0)


def test_crack_to_raw_carries_basis_as_derived(c):
    gid = slab(c, "Slab", 300, 500)
    rid = v2cards.crack_to_raw(c, gid)
    r = row(c, "ungraded_cards", rid)
    check("crack_to_raw carries basis as basis_derived",
          r["basis_derived"] == 300.0 and r["basis_entered"] is None
          and r["purchase_price"] == 300.0, f"{r['basis_derived']} / {r['basis_entered']}")
    check("crack_to_raw leaves raw market NULL", r["market_value"] is None)
    check("crack_to_raw marks the slab cracked, not disposed",
          row(c, "graded_cards", gid)["status"] == "cracked"
          and row(c, "graded_cards", gid)["realized_gain"] is None)
    check("crack_to_raw writes card_events on both rows",
          events(c, "ungraded_cards", rid, "created") == 1
          and events(c, "graded_cards", gid, "status") == 1)


# ── lost / restore ─────────────────────────────────────────────────────────────

def test_mark_lost_books_loss_and_restore_clears_it(c):
    gid = slab(c, "Gone", 250, 400)
    res = v2cards.mark_lost(c, "graded_cards", gid, "stolen")
    r = row(c, "graded_cards", gid)
    check("mark_lost books realized_gain = -basis via the generated column",
          r["realized_gain"] == -250.0 and res["realized_loss"] == -250.0,
          f"{r['realized_gain']} / {res['realized_loss']}")
    check("mark_lost writes card_events", events(c, "graded_cards", gid, "status") == 1)

    v2cards.restore_card(c, "graded_cards", gid)
    r = row(c, "graded_cards", gid)
    check("restore_card clears the realized loss",
          r["status"] == "active" and r["realized_gain"] is None
          and r["disposal_proceeds"] is None and "[lost" not in r["notes"],
          f"{r['status']} / {r['realized_gain']} / {r['notes']!r}")
    check("restore_card writes card_events", events(c, "graded_cards", gid, "status") == 2)

    unk = slab(c, "Unknown gone", None)
    v2cards.mark_lost(c, "graded_cards", unk)
    check("mark_lost on an unknown basis leaves realized_gain NULL",
          row(c, "graded_cards", unk)["realized_gain"] is None)


# ── PSA order import ───────────────────────────────────────────────────────────

_ORDER = ("Cert #,Type,Description,Grade,After Service\n"
          "5001,Card,1999 POKEMON GAME 4 CHARIZARD-HOLO,MINT 9,Shipped to you\n"
          "5002,Card,1999 POKEMON GAME 2 BLASTOISE-HOLO,GEM MT 10,Shipped to you\n")


def test_apply_order_is_all_or_nothing(c):
    """A failure on the 2nd card must leave the 1st unpromoted: a half-applied
    40-card import with no report is worse than none."""
    a, b = raw(c, "Charizard", 100), raw(c, "Blastoise", 80)
    rows = psa_orders.read_order(_ORDER)
    pairs = [{"cert": "5001", "card_id": a}, {"cert": "5002", "card_id": b}]
    real, calls = v2cards.promote_raw, []

    def flaky(conn, *args, **kw):
        calls.append(kw.get("_commit", True))
        if len(calls) == 2:
            raise RuntimeError("simulated failure on the 2nd card")
        return real(conn, *args, **kw)

    v2cards.promote_raw = flaky
    try:
        failed = False
        try:
            psa_orders.apply_order(c, rows, pairs, order_id="T1", grading_cost_each=20)
        except RuntimeError:
            failed = True
    finally:
        v2cards.promote_raw = real
    check("apply_order re-raises the failure", failed)
    check("apply_order calls promote_raw with _commit=False", calls == [False, False], str(calls))
    check("apply_order rollback: 1st card not promoted",
          row(c, "ungraded_cards", a)["status"] == "active"
          and c.execute("SELECT COUNT(*) FROM graded_cards").fetchone()[0] == 0)
    check("apply_order rollback: no card_events left behind",
          c.execute("SELECT COUNT(*) FROM card_events WHERE field != 'created'").fetchone()[0] == 0)

    res = psa_orders.apply_order(c, rows, pairs, order_id="T1", grading_cost_each=20)
    check("apply_order promotes the whole order when nothing fails",
          len(res["promoted"]) == 2
          and c.execute("SELECT COUNT(*) FROM graded_cards").fetchone()[0] == 2)
    check("apply_order commits its work", not c.in_transaction)


TESTS = [test_dashboard_keeps_pc_on_its_own_line, test_snapshot_records_holding,
         test_top_movers_ignore_copied_points,
         test_reprice_and_confirm_refuse_cards_not_held,
         test_create_card_blank_basis_and_market_stay_null,
         test_promote_raw_unknown_basis_stays_unknown,
         test_crack_to_raw_carries_basis_as_derived,
         test_mark_lost_books_loss_and_restore_clears_it,
         test_apply_order_is_all_or_nothing]


def main():
    print("card operation regressions")
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
