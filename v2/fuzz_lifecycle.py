"""
CardVault v2 — property-based fuzzer for the card lifecycle.

fuzz.py covers the deal engine. This covers everything else that mutates a
card: promote, crack, remove, lost, restore, reprice, create, bulk grading
updates, expected grades, PC flags, price confirmation.

It fires random legal operations in random order and asserts, after every one,
that the world still makes sense — plus it calls every read-side aggregate the
dashboard and reports depend on, because a screen that crashes on odd data is
just as broken as a wrong number.

Run:  python3 -m v2.fuzz_lifecycle
      python3 -m v2.fuzz_lifecycle --seed 12345 -n 200
"""

import argparse
import random
import sys
import traceback

from . import cards as v2cards
from . import checks as v2checks
from . import db as v2db

PENNY = 0.011

# every read-side function a screen depends on; none may raise, whatever the data
READERS = [
    ("dashboard_stats", lambda c: v2cards.dashboard_stats(c)),
    ("dashboard_extras", lambda c: v2cards.dashboard_extras(c)),
    ("list_cards", lambda c: v2cards.list_cards(c)),
    ("list_cards+gone", lambda c: v2cards.list_cards(c, include_gone=True)),
    ("list_cards+disposed", lambda c: v2cards.list_cards(c, include_disposed=True)),
    ("prediction_stats", lambda c: v2cards.prediction_stats(c)),
    ("trade_stats", lambda c: v2cards.trade_stats(c)),
    ("top_movers", lambda c: v2cards.top_movers(c)),
    ("at_grading", lambda c: v2cards.at_grading(c)),
    ("psa_capital", lambda c: v2cards.psa_capital(c)),
    ("sell_candidates", lambda c: v2cards.sell_candidates(c)),
    ("cash_summary", lambda c: v2cards.cash_summary(c)),
    ("snapshots", lambda c: v2cards.snapshots(c)),
]


def fresh_db():
    """A throwaway database built from the schema this code defines (v2/db.py),
    never copied from the live database — the fuzzer must never touch it."""
    return v2db.scratch_database(prefix="cv_life_")


def seed(c, rng, n=25):
    for i in range(n):
        unknown = rng.random() < 0.15
        basis = None if unknown else round(
            rng.choice([0, 5, 250, 3000, 15000]) * rng.uniform(0.4, 1.6), 2)
        mv = None if rng.random() < 0.15 else round(max(1.0, (basis or 1.0) * rng.uniform(0.2, 4.0)), 2)
        if rng.random() < 0.5:
            c.execute("INSERT INTO ungraded_cards (card_name, basis_entered, purchase_date, "
                      "market_value, status, date_added, grading_status, notes, "
                      "set_name, card_number, year) VALUES (?,?,'2026-01-01',?,?,'2026-01-01',?,'','','','')",
                      (f"raw{i}", basis, mv,
                       rng.choice(["active", "submitted_for_grading"]),
                       rng.choice(["Not Slated", "Slated", "At Grading"])))
        else:
            c.execute("INSERT INTO graded_cards (card_name, grading_company, grade, "
                      "basis_entered, acquisition_date, market_value, status, "
                      "date_added, notes, set_name, card_number, grading_fee) "
                      "VALUES (?,?,?,?,'2026-01-01',?, 'active','2026-01-01','','','',?)",
                      (f"slab{i}", rng.choice(v2db.GRADING_COMPANIES),
                       rng.choice(["10", "9", "8.5", "5"]), basis, mv,
                       round(rng.choice([0, 20, 80, 350]), 2)))
    c.commit()


def ids(c, table, statuses):
    q = ",".join("?" * len(statuses))
    return [r[0] for r in c.execute(f"SELECT id FROM {table} WHERE status IN ({q})", statuses)]


# ── operations ─────────────────────────────────────────────────────────────────

def op(rng, c, lineage):
    """Perform one random legal-ish lifecycle operation.

    Records real parent->child ids in `lineage`. Identifying a promotion's
    descendant by card name looked fine and was wrong: the fuzzer creates
    duplicate names, and cracking makes a raw row with the graded row's name,
    so a name lookup happily returns an unrelated card. That is the same
    inference mistake that put one PSA cert on two Umbreons.
    """
    held_raw = ids(c, "ungraded_cards", ("active", "submitted_for_grading"))
    held_slab = ids(c, "graded_cards", ("active",))
    gone_raw = ids(c, "ungraded_cards", ("removed", "lost"))
    gone_slab = ids(c, "graded_cards", ("removed", "lost"))
    choices = []
    if held_raw:
        choices += ["promote", "reprice_raw", "expected_grade", "grading_status",
                    "remove_raw", "lost_raw", "bulk", "expected_back", "pc_raw",
                    "stamp_cert"]
    if held_slab:
        choices += ["crack", "reprice_slab", "remove_slab", "lost_slab", "pc_slab", "confirm"]
    if gone_raw:
        choices.append("restore_raw")
    if gone_slab:
        choices.append("restore_slab")
    choices += ["create_raw", "create_slab"]
    what = rng.choice(choices)

    if what == "stamp_cert":
        # the real workflow: a PSA order is reconciled and the cert lands on the
        # raw card while it is still at grading, days before the box arrives
        c.execute("UPDATE ungraded_cards SET psa_cert=? WHERE id=?",
                  (str(rng.randrange(10**8)), rng.choice(held_raw)))
        c.commit()
    elif what == "promote":
        src = rng.choice(held_raw)
        # if a cert was already stamped on the raw row, the slab carries that
        # same number — anything else would invent a second physical card
        pre = c.execute("SELECT TRIM(COALESCE(psa_cert,'')) FROM ungraded_cards WHERE id=?",
                        (src,)).fetchone()[0]
        gid = v2cards.promote_raw(c, src, grading_company=rng.choice(v2db.GRADING_COMPANIES),
                                  grade=rng.choice(["10", "9", "8", "N5"]),
                                  serial_number=pre or str(rng.randrange(10**8)),
                                  grading_cost=round(rng.choice([0, 20, 80, 350]) * rng.uniform(0, 2), 2))
        lineage["promoted"][src] = gid
    elif what == "crack":
        src = rng.choice(held_slab)
        rid = v2cards.crack_to_raw(c, src, target_company=rng.choice(v2db.GRADING_COMPANIES),
                                   grading_status=rng.choice(["Not Slated", "Slated", "At Grading"]))
        lineage["cracked"][src] = rid
    elif what == "reprice_raw":
        v2cards.reprice(c, rng.choice(held_raw), rng.choice([None, 0.0, round(rng.uniform(0, 90000), 2)]),
                        "ungraded_cards")
    elif what == "reprice_slab":
        v2cards.reprice(c, rng.choice(held_slab), rng.choice([None, 0.0, round(rng.uniform(0, 90000), 2)]),
                        "graded_cards")
    elif what == "confirm":
        v2cards.confirm_price(c, rng.choice(held_slab), "graded_cards")
    elif what == "expected_grade":
        v2cards.set_expected_grade(c, rng.choice(held_raw), rng.choice(["", "10", "9", "7"]),
                                   sub_type=rng.choice([None, "banker", "casino"]))
    elif what == "expected_back":
        v2cards.set_expected_back(c, rng.choice(held_raw), rng.choice(["", "2026-12-01"]))
    elif what == "grading_status":
        v2cards.set_grading_status(c, rng.choice(held_raw),
                                   rng.choice(["Not Slated", "Slated", "At Grading"]))
    elif what == "bulk":
        k = rng.sample(held_raw, min(len(held_raw), rng.randint(1, 4)))
        v2cards.bulk_grading_update(c, k, grading_status=rng.choice([None, "Slated", "At Grading"]),
                                    target_company=rng.choice([None, "PSA", "TAG"]))
    elif what in ("remove_raw", "remove_slab"):
        t = "ungraded_cards" if what.endswith("raw") else "graded_cards"
        v2cards.remove_card(c, t, rng.choice(held_raw if t == "ungraded_cards" else held_slab), "fuzz")
    elif what in ("lost_raw", "lost_slab"):
        t = "ungraded_cards" if what.endswith("raw") else "graded_cards"
        v2cards.mark_lost(c, t, rng.choice(held_raw if t == "ungraded_cards" else held_slab),
                          rng.choice(v2db.LOST_REASONS))
    elif what in ("restore_raw", "restore_slab"):
        t = "ungraded_cards" if what.endswith("raw") else "graded_cards"
        v2cards.restore_card(c, t, rng.choice(gone_raw if t == "ungraded_cards" else gone_slab))
    elif what in ("pc_raw", "pc_slab"):
        t = "ungraded_cards" if what.endswith("raw") else "graded_cards"
        v2cards.set_pc(c, t, rng.choice(held_raw if t == "ungraded_cards" else held_slab),
                       rng.random() < 0.5)
    elif what == "create_raw":
        v2cards.create_card(c, "ungraded_cards", card_name=f"new{rng.randrange(10**6)}",
                            basis=rng.choice([None, 0.0, round(rng.uniform(0, 5000), 2)]),
                            market_value=rng.choice([None, round(rng.uniform(0, 5000), 2)]))
    elif what == "create_slab":
        v2cards.create_card(c, "graded_cards", card_name=f"new{rng.randrange(10**6)}",
                            grading_company=rng.choice(v2db.GRADING_COMPANIES), grade="10",
                            basis=rng.choice([None, 0.0, round(rng.uniform(0, 5000), 2)]),
                            market_value=rng.choice([None, round(rng.uniform(0, 5000), 2)]))
    return what


# ── properties ─────────────────────────────────────────────────────────────────

def props(c, before_counts, lineage):
    bad = []

    # L1 — cards are never destroyed, only re-statused
    for t, n0 in before_counts.items():
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        if n < n0:
            bad.append(f"L1 {t}: row count fell {n0} -> {n}; cards must never be deleted")

    # L2 — promotion carries basis exactly: graded basis = raw basis + grading
    # fee, but only when the raw basis was known — an unknown raw basis must
    # yield an unknown slab basis (the fee alone is not what the card cost),
    # never 0 + fee.
    for raw_id, gid in lineage["promoted"].items():
        r = c.execute("SELECT purchase_price b, basis_unknown u, status FROM ungraded_cards WHERE id=?",
                      (raw_id,)).fetchone()
        g = c.execute("SELECT acquisition_price ap, grading_fee f FROM graded_cards WHERE id=?", (gid,)).fetchone()
        if g is None:
            bad.append(f"L2 raw#{raw_id} promoted to graded#{gid} which does not exist")
            continue
        if r["status"] == "promoted":
            if r["u"]:
                if g["ap"] is not None:
                    bad.append(f"L2 raw#{raw_id}->graded#{gid}: raw basis unknown "
                               f"but slab basis {g['ap']} (should be unknown)")
            elif abs(float(g["ap"] or 0) - (float(r["b"] or 0) + float(g["f"] or 0))) > PENNY:
                bad.append(f"L2 raw#{raw_id}->graded#{gid}: basis {g['ap']} != raw {r['b']} + fee {g['f']}")

    # L3 — cracking carries basis intact and leaves the slab as history, not a sale
    for gid, raw_id in lineage["cracked"].items():
        g = c.execute("SELECT acquisition_price ap, status, realized_gain rg FROM graded_cards WHERE id=?", (gid,)).fetchone()
        r = c.execute("SELECT purchase_price b FROM ungraded_cards WHERE id=?", (raw_id,)).fetchone()
        if r is None:
            bad.append(f"L3 graded#{gid} cracked to raw#{raw_id} which does not exist")
            continue
        if g["status"] == "cracked":
            if abs(float(r["b"] or 0) - float(g["ap"] or 0)) > PENNY:
                bad.append(f"L3 graded#{gid}->raw#{raw_id}: basis {r['b']} != slab basis {g['ap']}")
            if g["rg"] is not None:
                bad.append(f"L3 graded#{gid}: cracked is not a disposal but booked gain {g['rg']}")

    # L4 — basis is never negative
    for t, col in (("graded_cards", "acquisition_price"), ("ungraded_cards", "purchase_price")):
        for r in c.execute(f"SELECT id, {col} v FROM {t} WHERE {col} < 0"):
            bad.append(f"L4 {t}#{r['id']}: negative basis {r['v']}")

    # L5 — a restored card is genuinely held again, with no disposal residue
    for t in ("graded_cards", "ungraded_cards"):
        for r in c.execute(f"SELECT id, status, realized_gain g, disposal_proceeds p FROM {t} "
                           f"WHERE status IN ('active','submitted_for_grading') "
                           f"AND (realized_gain IS NOT NULL OR disposal_proceeds IS NOT NULL)"):
            bad.append(f"L5 {t}#{r['id']}: held again but still carries gain={r['g']} proceeds={r['p']}")

    # L6 — a lost card with known basis books exactly minus its basis
    for t, col in (("graded_cards", "acquisition_price"), ("ungraded_cards", "purchase_price")):
        for r in c.execute(f"SELECT id, {col} b, realized_gain g FROM {t} "
                           f"WHERE status='lost' AND basis_unknown=0 AND realized_gain IS NOT NULL"):
            if abs(float(r["g"]) + float(r["b"] or 0)) > PENNY:
                bad.append(f"L6 {t}#{r['id']}: lost, basis {r['b']} but gain {r['g']} (want {-float(r['b'] or 0)})")

    # L7 — every read-side screen survives this data
    for name, fn in READERS:
        try:
            fn(c)
        except Exception as e:
            bad.append(f"L7 {name} raised {type(e).__name__}: {e}")

    # L8 — the standing integrity invariants
    for fn in v2checks.CHECKS:
        try:
            bad += [f"L8 {fn.__name__}: {v}" for v in fn(c)]
        except Exception as e:
            bad.append(f"L8 {fn.__name__} raised {type(e).__name__}: {e}")
    return bad


def run_sequence(s, n_ops, stop_at=None):
    rng = random.Random(s)
    c = fresh_db()
    seed(c, rng)
    lineage = {"promoted": {}, "cracked": {}}
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("graded_cards", "ungraded_cards")}
    try:
        for i in range(n_ops):
            if stop_at is not None and i >= stop_at:
                break
            try:
                what = op(rng, c, lineage)
            except ValueError:
                continue                       # refusing an illegal op is correct
            except Exception as e:
                return False, i, f"unexpected {type(e).__name__}: {e}\n{traceback.format_exc()}"
            v = props(c, counts, lineage)
            if v:
                return False, i, f"after {what}: " + "; ".join(v[:4])
        return True, n_ops, ""
    finally:
        c.close()


def shrink(s, fail_at):
    lo, hi, best = 0, fail_at + 1, fail_at + 1
    while lo < hi:
        mid = (lo + hi) // 2
        ok, _, _ = run_sequence(s, fail_at + 1, stop_at=mid)
        if ok:
            lo = mid + 1
        else:
            best, hi = mid, mid
    return best


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=150)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args(argv)
    seeds = [a.seed] if a.seed is not None else [random.randrange(10**9) for _ in range(a.runs)]
    print(f"fuzzing the card lifecycle — {len(seeds)} run(s) x up to {a.n} operations")
    failures = []
    for s in seeds:
        ok, at, detail = run_sequence(s, a.n)
        if ok:
            print(f"  ok     seed {s}")
        else:
            print(f"  FAIL   seed {s}  at op #{at}  (shrinks to {shrink(s, at)} op(s))")
            print(f"           {detail}")
            print(f"           replay: python3 -m v2.fuzz_lifecycle --seed {s} -n {at + 1}")
            failures.append(s)
    print(f"\n{len(seeds) - len(failures)}/{len(seeds)} runs clean")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
