"""
CardVault v2 — route-level fuzzer.

The model fuzzers prove the data stays coherent. This proves the app in front of
it does not fall over: every page renders, and every API endpoint refuses bad
input cleanly instead of throwing a 500.

A 500 is the specific failure being hunted. A 400 is fine — that is the endpoint
doing its job. A 200 on nonsense is worth knowing about too, so those are
reported separately rather than failed on.

Points the app at a throwaway database seeded with awkward data (unknown basis,
missing market values, lost and removed cards, a promoted card, a cracked one),
so the screens are exercised against the shapes that actually break them.

Run:  python3 -m v2.fuzz_http
      python3 -m v2.fuzz_http --seed 123
"""

import argparse
import random
import sys
import traceback
from pathlib import Path

from . import db as v2db

# Payload shapes aimed at the usual suspects: missing keys, wrong types,
# negatives, absurd magnitudes, injection-ish strings, unicode, nulls.
JUNK = [
    {}, {"id": None}, {"id": "abc"}, {"id": -1}, {"id": 10**12},
    {"id": 1, "table": "does_not_exist"}, {"id": 1, "table": "graded_cards'; DROP TABLE deals;--"},
    {"id": 1, "market_value": "not a number"}, {"id": 1, "market_value": -99999},
    {"id": 1, "market_value": float("1e309")}, {"id": 1, "value": "𝕦𝕟𝕚𝕔𝕠𝕕𝕖 🃏"},
    {"card_id": 1, "table": "ungraded_cards", "market_value": None},
    {"ids": []}, {"ids": [1, 2, "x"]}, {"ids": None},
    {"cards_out": [], "cards_in": [], "cash_amount": 0},
    {"cards_out": [{"table": "graded_cards", "card_id": 999999, "deal_value": 1}], "cash_amount": 5},
    {"cash_amount": "lots"}, {"amount": -0.0}, {"reason": "x" * 5000},
    {"is_pc": "maybe"}, {"grade": "", "grading_company": ""},
    {"expected_grade": "11"}, {"grading_status": "Teleported"},
    {"pairs": [{"cert": None, "card_id": None}]}, {"csv": ""}, {"csv": "not,a,psa,file"},
]


def build_db(rng):
    """Throwaway database with the awkward shapes deliberately present, built
    from the schema this code defines (v2/db.py) rather than copied from the
    live database. Returns the file path so the Flask app can be pointed at it."""
    c = v2db.scratch_database(prefix="cv_http_")
    path = Path(c.execute("PRAGMA database_list").fetchone()["file"])
    for i in range(12):
        unknown = i % 4 == 0
        mv = None if i % 5 == 0 else round(rng.uniform(0, 9000), 2)
        c.execute("INSERT INTO graded_cards (card_name, grading_company, grade, basis_entered, "
                  "acquisition_date, market_value, status, date_added, notes, "
                  "set_name, card_number, is_pc, grading_fee) "
                  "VALUES (?,'PSA','10',?, '2026-01-01',?,?, '2026-01-01','','','',?,?)",
                  (f"slab{i}", None if unknown else round(rng.uniform(0, 9000), 2), mv,
                   ["active", "active", "disposed", "cracked", "removed", "lost"][i % 6],
                   1 if i % 7 == 0 else 0, 20.0))
        c.execute("INSERT INTO ungraded_cards (card_name, basis_entered, purchase_date, market_value, "
                  "status, date_added, grading_status, notes, set_name, card_number, year) "
                  "VALUES (?,?, '2026-01-01',?,?, '2026-01-01',?,'','','','')",
                  (f"raw{i}", None if unknown else round(rng.uniform(0, 3000), 2), mv,
                   ["active", "submitted_for_grading", "promoted", "disposed", "removed", "lost"][i % 6],
                   ["Not Slated", "Slated", "At Grading"][i % 3]))
    # realized_gain is generated (schema 7): setting disposal_proceeds and a
    # 'disposed' status is enough for the gain to follow automatically.
    c.execute("UPDATE graded_cards SET disposal_proceeds=100 WHERE status='disposed'")
    c.execute("UPDATE ungraded_cards SET disposal_proceeds=50 WHERE status='disposed'")
    c.commit()
    c.close()
    return path


def routes(app):
    gets, posts = [], []
    for r in app.url_map.iter_rules():
        if r.endpoint == "static":
            continue
        path = str(r)
        if "<" in path:                       # fill converters with plausible values
            path = (path.replace("<int:deal_id>", "1").replace("<int:import_id>", "1")
                        .replace("<int:entry_id>", "1").replace("<path:name>", "nope.jpg")
                        .replace("<name>", "PSA"))
            if "<" in path:
                continue
        if "GET" in r.methods:
            gets.append(path)
        if "POST" in r.methods:
            posts.append(path)
    return sorted(set(gets)), sorted(set(posts))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args(argv)
    rng = random.Random(a.seed if a.seed is not None else random.randrange(10**9))

    path = build_db(rng)
    v2db.V2_DB_PATH = path                    # point the app at the throwaway
    from . import app as v2app
    v2app.app.config["TESTING"] = True
    client = v2app.app.test_client()
    gets, posts = routes(v2app.app)

    fails, soft = [], []
    print(f"GET — {len(gets)} routes must render")
    for p in gets:
        try:
            r = client.get(p)
            code = r.status_code
        except Exception as e:
            fails.append(f"GET {p} raised {type(e).__name__}: {e}")
            print(f"  ERROR  {p}  {type(e).__name__}: {e}")
            continue
        if code >= 500:
            fails.append(f"GET {p} -> {code}")
            print(f"  FAIL   {p}  {code}")
        elif code >= 400:
            print(f"  {code}    {p}")
        else:
            print(f"  ok     {p}  {code}")

    print(f"\nPOST — {len(posts)} endpoints x {len(JUNK)} junk payloads must not 500")
    for p in posts:
        worst, n500, n200 = 0, 0, 0
        for body in JUNK:
            try:
                r = client.post(p, json=body)
                code = r.status_code
            except Exception as e:
                fails.append(f"POST {p} {body} raised {type(e).__name__}: {e}")
                n500 += 1
                continue
            worst = max(worst, code)
            if code >= 500:
                n500 += 1
                if n500 == 1:
                    fails.append(f"POST {p} -> {code} on {body}")
            elif code < 300:
                n200 += 1
        if n500:
            print(f"  FAIL   {p}  {n500}/{len(JUNK)} payloads 500'd")
        else:
            note = f"  ({n200} accepted)" if n200 else ""
            print(f"  ok     {p}  worst {worst}{note}")
            if n200:
                soft.append(f"POST {p} accepted {n200} junk payload(s)")

    print()
    if soft:
        print("worth a look — endpoints that accepted malformed input:")
        for s in soft:
            print(f"   {s}")
        print()
    if fails:
        print(f"{len(fails)} FAILURE(S)")
        for f in fails[:20]:
            print(f"   {f}")
        return 1
    print("no 500s — every route survived")
    return 0


if __name__ == "__main__":
    sys.exit(main())
