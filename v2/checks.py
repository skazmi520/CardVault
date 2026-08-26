"""
CardVault v2 — data integrity invariants.

Every check here exists because something actually went wrong. They run
read-only against any v2 database, including the live one, so a bad deal entry
is caught the same day rather than three weeks later when the numbers stop
making sense.

Each check returns a list of violation strings. Empty list means it passed.

Run:  python3 -m v2.checks            (live database)
      python3 -m v2.checks path.db    (any other v2 database)
"""

import sqlite3
import sys

from . import db as v2db

TOLERANCE = 0.05          # 5%, matching the deal engine's own reconcile tolerance
PENNY = 0.01


def _rows(conn, q, *a):
    return conn.execute(q, a).fetchall()


# ── the invariants ─────────────────────────────────────────────────────────────

def pure_sale_proceeds_match_cash(conn):
    """A sale with nothing coming back must book exactly the cash received.

    Booking market value instead turned a $X loss on the Buyer deal into a
    $X phantom profit. Deals annotated [unreconciled] are exempt: their cash
    trail was never recorded, and the proceeds on the cards are the real ones."""
    bad = []
    for d in _rows(conn, "SELECT id, cash_amount, COALESCE(notes,'') notes FROM deals"):
        if "[unreconciled]" in d["notes"]:
            continue
        cash = float(d["cash_amount"] or 0)
        if cash == 0:
            continue                       # ambiguous: giveaway or unlogged cash
        out = sum(float(r[0] or 0) for r in _rows(conn,
            "SELECT COALESCE(SUM(disposal_proceeds),0) FROM graded_cards WHERE disposed_via_deal_id=? "
            "UNION ALL SELECT COALESCE(SUM(disposal_proceeds),0) FROM ungraded_cards "
            "WHERE disposed_via_deal_id=?", d["id"], d["id"]))
        n_in = sum(int(r[0]) for r in _rows(conn,
            "SELECT COUNT(*) FROM graded_cards WHERE acquired_via_deal_id=? "
            "UNION ALL SELECT COUNT(*) FROM ungraded_cards WHERE acquired_via_deal_id=?",
            d["id"], d["id"]))
        if n_in == 0 and out > 0 and abs(out - cash) > PENNY:
            bad.append(f"deal {d['id']}: booked ${out:,.2f} of proceeds against "
                       f"${cash:,.2f} cash received (${out - cash:+,.2f})")
    return bad


def certs_are_unique(conn):
    """One cert number, one card AT A TIME.

    Name-matching a PSA order once wrote the same cert onto two different
    Umbreons and two different Espeons, which is what this catches. But a cert
    legitimately appears on two rows when a card is sold and bought back later
    — the Gengar VMAX sold 2026-06-27 and reacquired 2026-07-18 is one physical
    card owned twice. So the violation is overlapping ownership, not repetition.
    """
    owners = {}
    for t, cert_col in (("graded_cards", "serial_number"), ("ungraded_cards", "psa_cert")):
        acq = "acquisition_date" if t == "graded_cards" else "purchase_date"
        for r in _rows(conn,
            f"SELECT id, card_name, TRIM({cert_col}) cert, {acq} acq, disposed_at, status "
            f"FROM {t} WHERE TRIM(COALESCE({cert_col},'')) <> '' AND status <> 'removed'"):
            owners.setdefault(r["cert"], []).append(
                (f"{t.split('_')[0]}#{r['id']}", r["card_name"],
                 (r["acq"] or "")[:10], (r["disposed_at"] or "")[:10]))

    bad = []
    for cert, holds in owners.items():
        if len(holds) < 2:
            continue
        for i in range(len(holds)):
            for j in range(i + 1, len(holds)):
                a, b = holds[i], holds[j]
                # held simultaneously if neither was disposed before the other
                # was acquired; a blank disposal date means still held
                a_open = a[3] or "9999"
                b_open = b[3] or "9999"
                if a[2] < b_open and b[2] < a_open:
                    bad.append(f"cert {cert} held by two cards at once: "
                               f"{a[0]} {a[1]} ({a[2]}→{a[3] or 'held'}) and "
                               f"{b[0]} {b[1]} ({b[2]}→{b[3] or 'held'})")
    return bad


def unknown_basis_books_no_gain(conn):
    """A card with no recorded cost has no knowable gain. Inventing one (usually
    as proceeds-minus-zero) silently inflates lifetime profit."""
    bad = []
    for t in ("graded_cards", "ungraded_cards"):
        for r in _rows(conn, f"SELECT id, card_name, realized_gain FROM {t} "
                             f"WHERE basis_unknown=1 AND realized_gain IS NOT NULL"):
            bad.append(f"{t}#{r['id']} {r['card_name']}: basis unknown but "
                       f"realized_gain = {r['realized_gain']}")
    return bad


def gain_equals_proceeds_minus_basis(conn):
    """Arithmetic. Catches a partial edit that moved one field and not the other."""
    bad = []
    for t, bcol in (("graded_cards", "acquisition_price"), ("ungraded_cards", "purchase_price")):
        for r in _rows(conn,
            f"SELECT id, card_name, {bcol} basis, disposal_proceeds p, realized_gain g "
            f"FROM {t} WHERE basis_unknown=0 AND realized_gain IS NOT NULL "
            f"AND disposal_proceeds IS NOT NULL"):
            want = round(float(r["p"]) - float(r["basis"] or 0), 2)
            if abs(want - float(r["g"])) > PENNY:
                bad.append(f"{t}#{r['id']} {r['card_name']}: gain {r['g']:,.2f} but "
                           f"proceeds {r['p']:,.2f} - basis {r['basis']:,.2f} = {want:,.2f}")
    return bad


def active_cards_are_not_disposed(conn):
    """A card cannot be in inventory and sold at the same time."""
    bad = []
    for t in ("graded_cards", "ungraded_cards"):
        for r in _rows(conn, f"SELECT id, card_name, status FROM {t} "
                             f"WHERE status IN ('active','submitted_for_grading') "
                             f"AND (disposed_via_deal_id IS NOT NULL "
                             f"     OR disposal_proceeds IS NOT NULL)"):
            bad.append(f"{t}#{r['id']} {r['card_name']}: status={r['status']} "
                       f"but carries disposal fields")
    return bad


def disposed_cards_have_proceeds(conn):
    """A sold card with no proceeds is a hole in the P&L."""
    bad = []
    for t in ("graded_cards", "ungraded_cards"):
        for r in _rows(conn, f"SELECT id, card_name FROM {t} "
                             f"WHERE status='disposed' AND disposal_proceeds IS NULL"):
            bad.append(f"{t}#{r['id']} {r['card_name']}: disposed with no proceeds")
    return bad


def statuses_are_known(conn):
    """Guards against a typo'd status quietly hiding a card from every screen."""
    bad = []
    ok = set(v2db.CARD_STATUSES)
    for t in ("graded_cards", "ungraded_cards"):
        for r in _rows(conn, f"SELECT DISTINCT status FROM {t}"):
            if r["status"] not in ok:
                bad.append(f"{t}: unknown status {r['status']!r}")
    return bad


def price_history_has_no_orphans(conn):
    """price_history is keyed to graded_cards; a hard DELETE would orphan rows."""
    return [f"price_history has {r['n']} rows pointing at missing cards"
            for r in _rows(conn,
                "SELECT COUNT(*) n FROM price_history p "
                "LEFT JOIN graded_cards g ON g.id=p.card_id WHERE g.id IS NULL")
            if r["n"]]


def trade_deals_balance(conn):
    """V_out must equal V_in + cash. Lifecycle rows are excluded: a card that
    was promoted then cracked keeps its original deal link at every stage, so
    counting all of them triple-counts one physical card."""
    bad = []
    for d in _rows(conn, "SELECT id, cash_amount, COALESCE(notes,'') notes FROM deals"):
        # [lifecycle] marks a deal whose in-side rows were later promoted or
        # cracked, so the same physical card appears more than once
        if "[unreconciled]" in d["notes"] or "[lifecycle]" in d["notes"]:
            continue
        i, cash = d["id"], float(d["cash_amount"] or 0)
        out = sum(float(r[0] or 0) for r in _rows(conn,
            "SELECT COALESCE(SUM(disposal_proceeds),0) FROM graded_cards WHERE disposed_via_deal_id=? "
            "UNION ALL SELECT COALESCE(SUM(disposal_proceeds),0) FROM ungraded_cards "
            "WHERE disposed_via_deal_id=?", i, i))
        inn = sum(float(r[0] or 0) for r in _rows(conn,
            "SELECT COALESCE(SUM(acquisition_price),0) FROM graded_cards "
            "WHERE acquired_via_deal_id=? AND status<>'cracked' "
            "UNION ALL SELECT COALESCE(SUM(purchase_price),0) FROM ungraded_cards "
            "WHERE acquired_via_deal_id=? AND status<>'promoted'", i, i))
        if out == 0 or inn == 0:
            continue                              # pure buy or pure sale, covered above
        expect = inn + cash
        denom = max(abs(out), abs(expect), 0.01)
        if abs(out - expect) / denom > TOLERANCE:
            bad.append(f"deal {i}: cards-out ${out:,.2f} vs cards-in + cash "
                       f"${expect:,.2f} (off ${out - expect:+,.2f})")
    return bad


CHECKS = [
    pure_sale_proceeds_match_cash,
    certs_are_unique,
    unknown_basis_books_no_gain,
    gain_equals_proceeds_minus_basis,
    active_cards_are_not_disposed,
    disposed_cards_have_proceeds,
    statuses_are_known,
    price_history_has_no_orphans,
    trade_deals_balance,
]


def run(conn) -> int:
    """Run every check. Returns the number of violations found."""
    total = 0
    for fn in CHECKS:
        try:
            bad = fn(conn)
        except Exception as e:                     # a broken check is itself a failure
            print(f"  ERROR  {fn.__name__}: {type(e).__name__}: {e}")
            total += 1
            continue
        if bad:
            total += len(bad)
            print(f"  FAIL   {fn.__name__}  ({len(bad)})")
            for b in bad[:10]:
                print(f"           {b}")
            if len(bad) > 10:
                print(f"           … and {len(bad) - 10} more")
        else:
            print(f"  ok     {fn.__name__}")
    return total


def main(argv):
    path = argv[1] if len(argv) > 1 else str(v2db.V2_DB_PATH)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    print(f"data integrity — {path}")
    n = run(conn)
    conn.close()
    print(f"\n{'PASS' if n == 0 else f'{n} VIOLATION(S)'}")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
