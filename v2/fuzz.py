"""
CardVault v2 — property-based fuzzer for the deal engine.

test_engine.py checks cases we thought of. This checks cases we did not: it
generates random but legal deals, runs them, and asserts that a set of
properties holds no matter what went in. Anything that breaks a property is a
bug we had not imagined.

Every run is seeded, so a failure is reproducible:  python3 -m v2.fuzz --seed 12345

On failure it shrinks — replays the same seed with fewer operations until it
finds the shortest sequence that still breaks — so the repro is debuggable
rather than a 200-deal haystack.

Run:  python3 -m v2.fuzz                 500 deals, random seed
      python3 -m v2.fuzz -n 5000         longer soak
      python3 -m v2.fuzz --seed 12345    replay a specific failure
"""

import argparse
import random
import sys
import traceback

from . import checks as v2checks
from . import db as v2db
from .deals import CardIn, CardOut, save_deal

PENNY = 0.011          # allow a cent of float noise before calling it a violation


# ── world building ─────────────────────────────────────────────────────────────

def fresh_db():
    """A throwaway database built from the schema this code defines (v2/db.py),
    never copied from the live database — the fuzzer must never touch it."""
    return v2db.scratch_database(prefix="cv_fuzz_")


def seed_cards(c, rng, n=40):
    """A starting inventory with the awkward shapes deliberately included:
    zero basis, unknown basis, no market value, and a raw card or two."""
    ids = []
    for i in range(n):
        unknown = rng.random() < 0.15
        basis = None if unknown else round(
            rng.choice([0, 1, 25, 300, 2500, 18000]) * rng.uniform(0.5, 1.5), 2)
        mv = None if rng.random() < 0.12 else round(max(1.0, (basis or 1.0) * rng.uniform(0.3, 3.0)), 2)
        if rng.random() < 0.25:
            cur = c.execute(
                "INSERT INTO ungraded_cards (card_name, basis_entered, purchase_date, market_value, "
                "status, date_added, grading_status, notes, set_name, card_number, year) "
                "VALUES (?,?,'2026-01-01',?,'active','2026-01-01','Not Slated','','','','')",
                (f"raw{i}", basis, mv))
            ids.append(("ungraded_cards", cur.lastrowid))
        else:
            cur = c.execute(
                "INSERT INTO graded_cards (card_name, grading_company, grade, basis_entered, "
                "acquisition_date, market_value, status, date_added, notes, set_name, card_number) "
                "VALUES (?,'PSA','10',?,'2026-01-01',?,'active','2026-01-01','','','')",
                (f"slab{i}", basis, mv))
            ids.append(("graded_cards", cur.lastrowid))
    c.commit()
    return ids


def live(c):
    out = []
    for t, col in (("graded_cards", "id"), ("ungraded_cards", "id")):
        for r in c.execute(f"SELECT {col} FROM {t} WHERE status IN ('active','submitted_for_grading')"):
            out.append((t, r[0]))
    return out


# ── deal generation ────────────────────────────────────────────────────────────

def gen_deal(rng, pool):
    """A random legal-ish deal. Deliberately reaches for the awkward shapes:
    zero cash, cash on both sides, unvalued lines, one-sided deals.

    2026-09-25: the engine got stricter — a blank take-in with cards going out
    needs either an ask (out_side_total) or every out card typed, or it is
    refused rather than defaulted to market. Most of the time this generator
    now supplies one of those so a plain trade still saves; sometimes it
    doesn't, to keep exercising the refusal path (run_sequence treats a
    ValueError as a legal, no-op outcome)."""
    shape = rng.choice(["pure_sale", "pure_buy", "trade", "trade_cash", "giveaway", "odd"])
    n_out = 0 if shape in ("pure_buy",) else rng.randint(1, min(4, max(1, len(pool))))
    n_out = min(n_out, len(pool))
    picks = rng.sample(pool, n_out) if n_out else []
    cards_out, cards_in, cash = [], [], 0.0

    for t, cid in picks:
        v = None if rng.random() < 0.15 else round(rng.uniform(0, 5000), 2)
        cards_out.append(CardOut(t, cid, v))

    if shape in ("pure_buy", "trade", "trade_cash", "odd"):
        for k in range(rng.randint(1, 3)):
            v = None if rng.random() < 0.15 else round(rng.uniform(0, 5000), 2)
            mv = None if rng.random() < 0.4 else round(rng.uniform(1, 6000), 2)
            cards_in.append(CardIn(card_name=f"in{rng.randrange(10**6)}",
                                   is_graded=rng.random() < 0.7, deal_value=v,
                                   market_value=mv))

    if shape == "pure_sale":
        cash = round(rng.uniform(1, 12000), 2)
    elif shape == "pure_buy":
        cash = -round(rng.uniform(1, 12000), 2)
    elif shape == "trade":
        cash = 0.0
    elif shape == "trade_cash":
        cash = round(rng.uniform(-8000, 8000), 2)
    elif shape == "giveaway":
        cards_in, cash = [], 0.0
    else:                                   # odd: anything goes, including absurd cash
        cash = round(rng.choice([0.0, 0.01, -0.01, rng.uniform(-50000, 50000)]), 2)

    # at least one side must have a card, or save_deal rightly refuses
    if not cards_out and not cards_in:
        cards_in = [CardIn(card_name="in_fallback", deal_value=100.0)]

    out_entered = [ln.deal_value for ln in cards_out]
    blanks_in = any(ln.deal_value is None for ln in cards_in)
    out_side_total = None
    if cards_out and blanks_in and not all(v is not None for v in out_entered) and rng.random() < 0.75:
        # a comfortable ask: covers the typed take-ins, cash and a healthy
        # per-blank share, so blank take-ins split to something positive
        # instead of tripping the "would book $0" / "cash exceeds ask" refusals
        typed_in_sum = sum(v for v in (ln.deal_value for ln in cards_in) if v is not None)
        n_blank_in = sum(1 for ln in cards_in if ln.deal_value is None)
        pool_target = typed_in_sum + n_blank_in * rng.uniform(200, 3000)
        out_side_total = round(cash + pool_target + rng.uniform(0, 500), 2)

    return dict(cards_out=cards_out, cards_in=cards_in, cash_amount=cash,
                out_side_total=out_side_total)


# ── properties that must always hold ───────────────────────────────────────────

def props_after_deal(c, spec, result):
    """Returns a list of violated properties for this one deal."""
    bad = []
    did = result["deal_id"]

    out_rows = []
    for t, bcol in (("graded_cards", "acquisition_price"), ("ungraded_cards", "purchase_price")):
        for r in c.execute(f"SELECT id, {bcol} basis, basis_unknown, disposal_proceeds p, "
                           f"realized_gain g, status FROM {t} WHERE disposed_via_deal_id=?", (did,)):
            out_rows.append((t, dict(r)))
    in_total = sum(float(r[0] or 0) for r in c.execute(
        "SELECT COALESCE(SUM(acquisition_price),0) FROM graded_cards WHERE acquired_via_deal_id=? "
        "UNION ALL SELECT COALESCE(SUM(purchase_price),0) FROM ungraded_cards "
        "WHERE acquired_via_deal_id=?", (did, did)))
    cash = spec["cash_amount"]
    out_total = sum(r["p"] or 0 for _, r in out_rows)

    # P1 — a pure sale books exactly the cash received
    if out_rows and not spec["cards_in"] and cash > 0:
        if abs(out_total - cash) > PENNY:
            bad.append(f"P1 pure sale: proceeds {out_total:.2f} != cash {cash:.2f}")

    # P2 — gain is proceeds minus basis, or None when basis is unknown
    for t, r in out_rows:
        if r["basis_unknown"]:
            if r["g"] is not None:
                bad.append(f"P2 {t}#{r['id']}: unknown basis but gain {r['g']}")
        elif r["g"] is not None and r["p"] is not None:
            want = round(float(r["p"]) - float(r["basis"] or 0), 2)
            if abs(want - float(r["g"])) > PENNY:
                bad.append(f"P2 {t}#{r['id']}: gain {r['g']} != {want}")

    # P3 — nothing books negative proceeds
    for t, r in out_rows:
        if r["p"] is not None and r["p"] < -PENNY:
            bad.append(f"P3 {t}#{r['id']}: negative proceeds {r['p']}")

    # P4 — every card that left is marked disposed
    for t, r in out_rows:
        if r["status"] != "disposed":
            bad.append(f"P4 {t}#{r['id']}: left in a deal but status={r['status']}")

    # P5 — incoming cards never carry negative basis
    if in_total < -PENNY:
        bad.append(f"P5 incoming basis total is negative: {in_total:.2f}")

    # P6 — a typed take-in price IS the basis. The engine may fill blanks but
    # must never rewrite a number the user typed on the receive side.
    for ln in spec["cards_in"]:
        if ln.deal_value is None:
            continue
        got = c.execute(
            "SELECT acquisition_price FROM graded_cards WHERE card_name=? AND acquired_via_deal_id=? "
            "UNION ALL SELECT purchase_price FROM ungraded_cards WHERE card_name=? AND acquired_via_deal_id=?",
            (ln.card_name, did, ln.card_name, did)).fetchone()
        if got is not None and abs(float(got[0]) - ln.deal_value) > PENNY:
            bad.append(f"P6 in-card {ln.card_name}: entered {ln.deal_value} "
                       f"but basis {got[0]}")

    # P7 — the owner's model: what the outgoing cards fetched is exactly what came
    # back, cash plus take-in. Whenever anything came back (cards in, or cash
    # either way) the identity holds, no tolerance, no "reconcile" escape
    # hatch. 2026-09-25: there is no clamp exception any more — a came-back
    # that would go negative (more cash paid than take-in) is refused outright
    # by resolve(), so if a deal saved at all the identity holds unconditionally.
    came_back = spec["cards_in"] or cash != 0
    if out_rows and came_back:
        slack = PENNY * (len(out_rows) + len(spec["cards_in"]) + 1)
        if abs(out_total - (in_total + cash)) > slack:
            bad.append(f"P7 proceeds {out_total:.2f} != cash {cash:.2f} + take-in {in_total:.2f}")

    # P8 — typed per-card out values set the SPLIT of the proceeds, never the
    # total: proceeds must be in the same proportion as the typed values.
    typed = [ln.deal_value for ln in spec["cards_out"]]
    if out_rows and came_back and len(typed) > 1 \
            and all(v is not None for v in typed) and sum(typed) > 0 and out_total > 0:
        by_id = {(t, r["id"]): r for t, r in out_rows}
        for ln in spec["cards_out"]:
            r = by_id.get((ln.table, ln.card_id))
            if r is None or r["p"] is None:
                continue
            want = out_total * ln.deal_value / sum(typed)
            if abs(float(r["p"]) - want) > PENNY * 2:
                bad.append(f"P8 {ln.table}#{ln.card_id}: typed {ln.deal_value} of {sum(typed):.2f} "
                           f"should take {want:.2f} of {out_total:.2f}, booked {r['p']}")

    # P9 — typed and derived never share a column: an in-card's basis_entered
    # is exactly the typed deal_value (or NULL when it was blank), market_value
    # is exactly the typed market (or NULL), and its deal_lines row agrees.
    for ln in spec["cards_in"]:
        row = c.execute(
            "SELECT id, basis_entered, market_value FROM graded_cards "
            "WHERE card_name=? AND acquired_via_deal_id=? "
            "UNION ALL SELECT id, basis_entered, market_value FROM ungraded_cards "
            "WHERE card_name=? AND acquired_via_deal_id=?",
            (ln.card_name, did, ln.card_name, did)).fetchone()
        if row is None:
            continue
        want_entered = round(ln.deal_value, 2) if ln.deal_value is not None else None
        got_entered = row["basis_entered"]
        if (got_entered is None) != (want_entered is None) or (
                want_entered is not None and abs(float(got_entered) - want_entered) > PENNY):
            bad.append(f"P9 in-card {ln.card_name}: typed {ln.deal_value} "
                       f"but basis_entered {got_entered}")
        want_mv = round(ln.market_value, 2) if (ln.market_value or 0) > 0 else None
        got_mv = row["market_value"]
        if (got_mv is None) != (want_mv is None) or (
                want_mv is not None and abs(float(got_mv) - want_mv) > PENNY):
            bad.append(f"P9 in-card {ln.card_name}: typed market {ln.market_value} "
                       f"but stored market_value {got_mv}")
        line = c.execute(
            "SELECT entered_value FROM deal_lines WHERE deal_id=? AND side='in' AND card_id=?",
            (did, row["id"])).fetchone()
        if line is not None:
            le = line["entered_value"]
            if (le is None) != (want_entered is None) or (
                    want_entered is not None and abs(float(le) - want_entered) > PENNY):
                bad.append(f"P9 in-card {ln.card_name}: deal_lines entered_value {le} "
                           f"!= typed {want_entered}")

    return bad


def props_global(c):
    """The integrity invariants the live database is held to — minus the trade
    balance check. The engine's contract for a trade whose typed sides do not
    reconcile is warn-and-save (the user's numbers are the user's numbers), so
    generated deals legally violate strict balance. On the live database the
    check stays: there an imbalance means a typo a human should look at.
    P7 below covers the balanced case the engine does guarantee."""
    bad = []
    for fn in v2checks.CHECKS:
        if fn is v2checks.trade_deals_balance:
            continue
        try:
            bad += [f"{fn.__name__}: {v}" for v in fn(c)]
        except Exception as e:
            bad.append(f"{fn.__name__} raised {type(e).__name__}: {e}")
    return bad


# ── the loop ───────────────────────────────────────────────────────────────────

def run_sequence(seed, n_ops, stop_at=None, verbose=False):
    """Run n_ops generated deals. Returns (ok, op_index, detail)."""
    rng = random.Random(seed)
    c = fresh_db()
    pool = seed_cards(c, rng)
    try:
        for i in range(n_ops):
            if stop_at is not None and i >= stop_at:
                break
            avail = live(c)
            if not avail:
                break
            spec = gen_deal(rng, avail)
            before = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
            try:
                res = save_deal(c, **spec)
            except ValueError:
                # a refusal is a legal outcome; it must leave nothing behind
                after = c.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
                if after != before:
                    return False, i, f"atomicity: refused deal still wrote {after - before} row(s)"
                continue
            except Exception as e:
                return False, i, f"unexpected {type(e).__name__}: {e}\n{traceback.format_exc()}"
            v = props_after_deal(c, spec, res) + props_global(c)
            if v:
                return False, i, "; ".join(v[:4])
        return True, n_ops, ""
    finally:
        c.close()


def shrink(seed, fail_at):
    """Find the shortest prefix of the same seeded run that still fails."""
    lo, hi = 0, fail_at + 1
    best = hi
    while lo < hi:
        mid = (lo + hi) // 2
        ok, _, _ = run_sequence(seed, fail_at + 1, stop_at=mid)
        if ok:
            lo = mid + 1
        else:
            best = mid
            hi = mid
    return best


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=500, help="deals per run")
    ap.add_argument("--runs", type=int, default=20, help="independent seeded runs")
    ap.add_argument("--seed", type=int, default=None, help="replay one seed")
    a = ap.parse_args(argv)

    seeds = [a.seed] if a.seed is not None else [random.randrange(10**9) for _ in range(a.runs)]
    failures = []
    print(f"fuzzing the deal engine — {len(seeds)} run(s) x up to {a.n} deals")
    for s in seeds:
        ok, at, detail = run_sequence(s, a.n)
        if ok:
            print(f"  ok     seed {s}")
        else:
            n = shrink(s, at)
            print(f"  FAIL   seed {s}  at deal #{at}  (shrinks to {n} deal(s))")
            print(f"           {detail}")
            print(f"           replay: python3 -m v2.fuzz --seed {s} -n {at + 1}")
            failures.append(s)
    print(f"\n{len(seeds) - len(failures)}/{len(seeds)} runs clean")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
