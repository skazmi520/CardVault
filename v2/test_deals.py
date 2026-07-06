"""
CardVault v2 — deal engine seed tests.

Creates a THROWAWAY database (schema cloned from the real v2 db, zero rows),
seeds fake cards, and runs one deal of each type, printing the computed
allocation, basis and gains so the math can be verified by hand.

The real v2 database is opened read-only for its schema and never written.

Run:  python3 -m v2.test_deals
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

from . import db as v2db
from .deals import CardOut, CardIn, save_deal, get_deal


def make_test_db() -> sqlite3.Connection:
    """Clone the real v2 schema (no rows) into a temp file, marked as v2."""
    src = sqlite3.connect(f"file:{v2db.V2_DB_PATH}?mode=ro", uri=True)
    ddl = [r[0] for r in src.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%'").fetchall()]
    src.close()

    path = Path(tempfile.mkdtemp(prefix="cardvault_test_")) / "test_v2.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for stmt in ddl:
        conn.execute(stmt)
    conn.execute("INSERT OR REPLACE INTO v2_meta (key,value) VALUES ('is_v2','1')")
    conn.commit()
    print(f"throwaway db: {path}\n")
    return conn


def seed_card(conn, name, basis, mv, graded=True):
    if graded:
        cur = conn.execute(
            """INSERT INTO graded_cards
                 (serial_number, grading_company, grade, card_name, card_number,
                  set_name, acquisition_type, acquisition_price, acquisition_date,
                  notes, date_added, market_value, status)
               VALUES ('', 'PSA', '10', ?, '', '', 'Cash', ?, '2026-01-01', '',
                       '2026-01-01', ?, 'active')""", (name, basis, mv))
    else:
        cur = conn.execute(
            """INSERT INTO ungraded_cards
                 (card_name, card_number, set_name, year, purchase_price,
                  purchase_date, acquisition_type, notes, grading_status,
                  target_grading_company, date_added, status)
               VALUES (?, '', '', '', ?, '2026-01-01', 'Cash', '', 'Not Slated',
                       '', '2026-01-01', 'active')""", (name, basis))
    conn.commit()
    return cur.lastrowid


def show(title, result):
    print(f"── {title} " + "─" * max(0, 66 - len(title)))
    print(f"   V_out=${result['v_out']:,.2f}  V_in=${result['v_in']:,.2f}  "
          f"cash={result['cash_amount']:+,.2f}")
    for w in result["warnings"]:
        print(f"   ⚠ {w}")
    for l in result["out_lines"]:
        print(f"   OUT {l['name']:<28} basis=${l['basis']:>9,.2f}  "
              f"proceeds=${l['proceeds']:>9,.2f}  gain=${l['realized_gain']:>+9,.2f}")
    for l in result["in_lines"]:
        print(f"   IN  {l['name']:<28} agreed=${l['agreed_value']:>9,.2f}  "
              f"basis=${l['basis']:>9,.2f}")
    print()


def main():
    conn = make_test_db()

    # seed inventory: (name, basis, market value)
    a = seed_card(conn, "Card A (mv 1000 / basis 600)", 600, 1000)
    b = seed_card(conn, "Card B (mv 500 / basis 450)",  450, 500)
    c = seed_card(conn, "Card C (mv 100)",              80,  100)
    d = seed_card(conn, "Card D (mv 300)",              200, 300)
    e = seed_card(conn, "Card E (mv 600)",              350, 600)
    r = seed_card(conn, "Raw R (basis 40)",             40,  None, graded=False)

    # 1. PURE CASH BUY: two cards in, per-line values, I pay $500
    show("1. Pure buy — 2 cards in, values 300/200, cash -500",
         save_deal(conn,
                   cards_in=[CardIn("Bought X", deal_value=300),
                             CardIn("Bought Y", is_graded=False, deal_value=200)],
                   cash_amount=-500, counterparty="Seed Vendor",
                   payment_method="cash"))

    # 2. PURE SALE: two cards out, no line values, $1000 cash received
    #    -> proceeds allocated pro rata by market value (100:300 -> 250/750)
    show("2. Pure sale — cards C+D out, no line values, cash +1000",
         save_deal(conn,
                   cards_out=[CardOut("graded_cards", c),
                              CardOut("graded_cards", d)],
                   cash_amount=1000, counterparty="Seed Buyer",
                   payment_method="cash"))

    # 3. TRADE, CASH TO ME: give A (basis 600), receive card @700 + $300 cash
    show("3. Trade w/ cash IN — give A, receive X@700 + $300 cash",
         save_deal(conn,
                   cards_out=[CardOut("graded_cards", a, deal_value=1000)],
                   cards_in=[CardIn("Traded-in X", deal_value=700)],
                   cash_amount=300, payment_method="trade"))

    # 4. TRADE, CASH FROM ME: give B (basis 450), receive card @800, pay $300
    show("4. Trade w/ cash OUT — give B@500, receive Y@800, cash -300",
         save_deal(conn,
                   cards_out=[CardOut("graded_cards", b, deal_value=500)],
                   cards_in=[CardIn("Traded-in Y", deal_value=800)],
                   cash_amount=-300, payment_method="mixed"))

    # 5. SIDE-TOTAL-ONLY ALLOCATION: give E + Raw R with only an out-side total,
    #    receive $900 cash. Weights: E mv=600, Raw weight=basis 40.
    show("5. Side-total only — E + RawR out, out_side_total=900, cash +900",
         save_deal(conn,
                   cards_out=[CardOut("graded_cards", e),
                              CardOut("ungraded_cards", r)],
                   out_side_total=900, cash_amount=900,
                   payment_method="cash"))

    # 6. RECONCILIATION WARNING: both sides itemized and >5% apart
    f = seed_card(conn, "Card F (mv 1000)", 700, 1000)
    show("6. Mismatch warning — give F@1000, receive Z@800, no cash (>5% gap)",
         save_deal(conn,
                   cards_out=[CardOut("graded_cards", f, deal_value=1000)],
                   cards_in=[CardIn("Traded-in Z", deal_value=800)],
                   cash_amount=0, payment_method="trade"))

    # invariants
    print("── invariants " + "─" * 53)
    n_deals = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    disposed = conn.execute(
        "SELECT COUNT(*) FROM graded_cards WHERE status='disposed'").fetchone()[0] + \
        conn.execute(
        "SELECT COUNT(*) FROM ungraded_cards WHERE status='disposed'").fetchone()[0]
    still_there = conn.execute(
        "SELECT COUNT(*) FROM graded_cards").fetchone()[0]
    print(f"   deals saved: {n_deals} | cards disposed (kept as history): {disposed} "
          f"| graded rows never deleted: {still_there}")

    # double-disposal must be refused
    try:
        save_deal(conn, cards_out=[CardOut("graded_cards", a)], cash_amount=100)
        print("   double-disposal: FAILED — accepted a disposed card!")
    except ValueError as ex:
        print(f"   double-disposal refused: {ex}")

    # deal detail lookup joins card lines back
    detail = get_deal(conn, 3)
    print(f"   get_deal(3): {len(detail['cards_out'])} out, "
          f"{len(detail['cards_in'])} in, cash {detail['deal']['cash_amount']:+,.2f}")

    conn.close()
    print("\nDONE — real v2 database untouched.")


if __name__ == "__main__":
    main()
