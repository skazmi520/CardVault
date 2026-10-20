"""
CardVault v2: CSV import regression tests (v2/importer.py).

Same shape as test_cards.py: every test ASSERTS, each against a throwaway
database built from the schema v2/db.py defines. The live database is never
opened.

Run:  python3 -m v2.test_import
"""

import sys
from datetime import date

from . import cards as v2cards
from . import db as v2db
from . import importer

FAILURES = []
PASSES = []

SLAB_HEAD = "Card name,Set,Card number,Year,Grader,Grade,Cert,Cost,Market value,Date acquired,Personal collection,Notes\n"
RAW_HEAD = "Card name,Set,Card number,Year,Cost,Market value,Grading status,Date acquired,Personal collection,Notes\n"


def check(name, cond, detail=""):
    (PASSES if cond else FAILURES).append(name)
    print(f"  {'ok    ' if cond else 'FAIL  '} {name}" + (f"\n           {detail}" if not cond and detail else ""))


def fresh_db():
    return v2db.scratch_database(prefix="cv_import_")


def slabs(*lines):
    return SLAB_HEAD + "\n".join(lines) + "\n"


def raws(*lines):
    return RAW_HEAD + "\n".join(lines) + "\n"


def count(c, table):
    return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def refused(fn, *a):
    try:
        fn(*a)
    except ValueError as e:
        return str(e)
    return None


def test_valid_rows_import_with_typed_basis(c):
    res = importer.apply(c, "slabs", slabs(
        "Charizard,Base Set,4,1999,psa,9,12345678,500,650,2025-03-14,no,note a",
        "Gengar,Fossil,5,1999,BGS,9.5,23456789,\"$1,200.50\",,,yes,"))
    check("two slabs created, none skipped", res == {"created": 2, "skipped": 0}, str(res))
    a = c.execute("SELECT * FROM graded_cards WHERE serial_number='12345678'").fetchone()
    check("slab keeps typed basis, grade, market and date",
          a["acquisition_price"] == 500 and a["basis_unknown"] == 0 and a["grade"] == "9"
          and a["market_value"] == 650 and a["acquisition_date"] == "2025-03-14"
          and a["notes"] == "note a" and a["set_name"] == "Base Set" and a["year"] == "1999")
    check("grader stored upper-case", a["grading_company"] == "PSA")
    b = c.execute("SELECT * FROM graded_cards WHERE serial_number='23456789'").fetchone()
    check("money with $ and comma parsed", b["acquisition_price"] == 1200.5)
    check("blank market value stays NULL (never filled from cost)", b["market_value"] is None)
    check("Personal collection yes => is_pc", b["is_pc"] == 1 and a["is_pc"] == 0)

    res = importer.apply(c, "raw", raws("Pikachu,Jungle,60,1999,30,45,,2025-04-01,no,light wear"))
    r = c.execute("SELECT * FROM ungraded_cards").fetchone()
    check("raw card created with typed basis and Not Slated",
          res["created"] == 1 and r["purchase_price"] == 30 and r["market_value"] == 45
          and r["grading_status"] == "Not Slated" and r["status"] == "active")
    check("import commits its work", not c.in_transaction)


def test_blank_cost_is_unknown_basis(c):
    text = slabs("Blastoise,Base Set,2,1999,CGC,BL 10,34567890,,300,,no,")
    p = importer.preview(c, "slabs", text)
    row = p["rows"][0]
    check("blank cost: row ok with the plain-language note",
          row["status"] == "ok" and "no cost: counted in inventory, left out of profit numbers" in row["notes"],
          str(row))
    check("blank date acquired defaults to today", row["acquired"] == date.today().isoformat())
    importer.apply(c, "slabs", text)
    s = c.execute("SELECT * FROM graded_cards").fetchone()
    check("blank cost stored as unknown basis, not $0",
          s["acquisition_price"] is None and s["basis_unknown"] == 1 and s["grade"] == "BL 10")
    importer.apply(c, "raw", raws("Mewtwo,Base Set,10,1999,,,,,,"))
    r = c.execute("SELECT * FROM ungraded_cards").fetchone()
    check("raw blank cost and market stay NULL",
          r["purchase_price"] is None and r["basis_unknown"] == 1 and r["market_value"] is None)


def test_row_problems_are_reported(c):
    def one(line, kind="slabs"):
        text = slabs(line) if kind == "slabs" else raws(line)
        return importer.preview(c, kind, text)["rows"][0]

    r = one("X,,,,PSA,10,,3.5k,,,,")
    check('"3.5k" cost refused', r["status"] == "error" and any("Cost" in e for e in r["errors"]), str(r["errors"]))
    check("refused cost does not also claim 'no cost'", not r["notes"], str(r["notes"]))
    for bad in ("1 200", "35O0", "-5"):
        check(f"cost {bad!r} refused", one(f"X,,,,PSA,10,,{bad},,,,")["status"] == "error")
    r = one(",Base Set,4,1999,PSA,10,,5,,,,")
    check("missing card name", r["status"] == "error" and "Card name is required" in r["errors"])
    r = one("X,,,,ZZZ,10,,5,,,,")
    check("bad grader", r["status"] == "error" and any("Grader" in e for e in r["errors"]))
    check("missing grade", one("X,,,,PSA,,,5,,,,")["status"] == "error")
    r = one("X,,,,PSA,10,,5,,2025-02-30,,")
    check("bad date", r["status"] == "error" and any("Date acquired" in e for e in r["errors"]))
    check("date in words is refused", one("X,,,,PSA,10,,5,,March 3rd,,")["status"] == "error")
    check("M/D/YYYY normalized", one("X,,,,PSA,10,,5,,3/2/2025,,")["acquired"] == "2025-03-02")
    check("M/D/YY normalized", one("X,,,,PSA,10,,5,,12/31/24,,")["acquired"] == "2024-12-31")
    check("bad Personal collection value", one("X,,,,PSA,10,,5,,,maybe,")["status"] == "error")
    for yes in ("yes", "Y", "TRUE", "x", "1"):
        check(f"PC {yes!r} => True", one(f"X,,,,PSA,10,,5,,,{yes},")["pc"] is True)
    for no in ("", "no", "N", "false", "0"):
        check(f"PC {no!r} => False", one(f"X,,,,PSA,10,,5,,,{no},")["pc"] is False)
    check("bad raw grading status", one("X,,,,5,,Sent,,,", "raw")["status"] == "error")
    check("raw grading status is case-insensitive",
          one("X,,,,5,,at grading,,,", "raw")["grading_status"] == "At Grading")
    r = one("X,,,,ZZZ,,,3.5k,,xx,maybe,")
    check("every problem on a row is listed, not just the first", len(r["errors"]) >= 5, str(r["errors"]))


def test_file_level_checks(c):
    p = importer.preview(c, "slabs", "Card name,Grade\nX,10\n")
    check("missing Grader column is a file error",
          any("Grader" in e for e in p["file_errors"]), str(p["file_errors"]))
    p = importer.preview(c, "raw", "Set,Cost\nBase,5\n")
    check("missing Card name column is a file error", any("Card name" in e for e in p["file_errors"]))
    p = importer.preview(c, "slabs", "﻿" + SLAB_HEAD.upper().replace("CARD NAME", " card name ")
                         + "Charizard,,,,psa,9,,5,,,,\n\n,,,,,,,,,,,\n")
    check("BOM, upper-case and padded headers accepted; blank rows skipped",
          not p["file_errors"] and len(p["rows"]) == 1 and p["rows"][0]["status"] == "ok", str(p))
    p = importer.preview(c, "raw", RAW_HEAD.strip() + ",Colour,Shelf\nX,,,,5,,,,,,red,A\n")
    check("unknown columns ignored and reported once",
          not p["file_errors"] and p["notes"] == ["Ignored columns: Colour, Shelf"], str(p["notes"]))
    p = importer.preview(c, "slabs", "")
    check("empty file is a file error", bool(p["file_errors"]))
    p = importer.preview(c, "slabs", SLAB_HEAD)
    check("header-only file is a file error", bool(p["file_errors"]))
    check("row line numbers count the header", importer.preview(
        c, "slabs", slabs("A,,,,PSA,9,,,,,,", "", "B,,,,PSA,9,,,,,,"))["rows"][1]["line"] == 4)
    check("unknown kind refused", refused(importer.preview, c, "cats", "x") is not None)


def test_raw_at_grading_matches_the_app(c):
    importer.apply(c, "raw", raws("Venusaur,Base Set,15,1999,80,120,Slated,,,",
                                  "Mewtwo,Base Set,10,1999,60,60,At Grading,,,"))
    s = c.execute("SELECT * FROM ungraded_cards WHERE card_name='Venusaur'").fetchone()
    a = c.execute("SELECT * FROM ungraded_cards WHERE card_name='Mewtwo'").fetchone()
    check("Slated stays active", s["grading_status"] == "Slated" and s["status"] == "active"
          and s["submitted_at"] is None)
    check("At Grading ends up submitted_for_grading with submitted_at stamped",
          a["status"] == "submitted_for_grading" and a["grading_status"] == "At Grading"
          and a["submitted_at"])
    # the same end state the app's own bulk update produces
    b = v2cards.create_card(c, "ungraded_cards", card_name="Own")
    v2cards.bulk_grading_update(c, [b], grading_status="At Grading")
    o = c.execute("SELECT * FROM ungraded_cards WHERE id=?", (b,)).fetchone()
    check("matches bulk_grading_update's fields",
          (o["status"], o["grading_status"]) == (a["status"], a["grading_status"]))


def test_duplicate_certs(c):
    v2cards.create_card(c, "graded_cards", card_name="Held", grade="9", serial_number="11111111")
    v2cards.create_card(c, "graded_cards", card_name="Sold one", grade="9", serial_number="22222222")
    c.execute("UPDATE graded_cards SET status='removed' WHERE serial_number='22222222'")
    c.commit()
    text = slabs("Again,,,,PSA,10,11111111,5,,,,",
                 "Gone,,,,PSA,10,22222222,5,,,,",
                 "Fresh,,,,PSA,10,33333333,5,,,,",
                 "Fresh twin,,,,PSA,10,33333333,5,,,,")
    p = importer.preview(c, "slabs", text)
    st = [r["status"] for r in p["rows"]]
    check("cert already held => duplicate; any status counts; in-file repeat => error",
          st == ["duplicate", "duplicate", "ok", "error"], str(st))
    check("duplicate row says why", "cert already in your collection" in p["rows"][0]["notes"])
    check("counts add up", p["counts"] == {"ok": 1, "error": 1, "duplicate": 2}, str(p["counts"]))
    check("the repeat points at the first row", "row 4" in p["rows"][3]["errors"][0], p["rows"][3]["errors"][0])

    ok_text = slabs("Again,,,,PSA,10,11111111,5,,,,", "Fresh,,,,PSA,10,33333333,5,,,,")
    res = importer.apply(c, "slabs", ok_text)
    check("duplicates skipped, new ones created", res == {"created": 1, "skipped": 1}, str(res))
    check("still one 11111111", c.execute(
        "SELECT COUNT(*) FROM graded_cards WHERE serial_number='11111111'").fetchone()[0] == 1)

    r = v2cards.create_card(c, "ungraded_cards", card_name="Raw at PSA")
    c.execute("UPDATE ungraded_cards SET psa_cert='44444444' WHERE id=?", (r,))
    c.commit()
    p = importer.preview(c, "slabs", slabs("Back,,,,PSA,9,44444444,5,,,,"))
    check("a raw card's psa_cert also counts as already held", p["rows"][0]["status"] == "duplicate")
    check("blank certs never collide", [x["status"] for x in importer.preview(
        c, "slabs", slabs("A,,,,PSA,9,,,,,,", "B,,,,PSA,9,,,,,,"))["rows"]] == ["ok", "ok"])


def test_apply_refuses_when_any_row_is_bad(c):
    text = slabs("Good,,,,PSA,10,55555555,5,,,,", "Bad,,,,NOPE,10,66666666,5,,,,")
    msg = refused(importer.apply, c, "slabs", text)
    check("apply refuses with a plain message", msg == "Fix the 1 row with problems and preview again", str(msg))
    check("nothing created", count(c, "graded_cards") == 0)
    msg = refused(importer.apply, c, "slabs", "Card name,Grade\nX,1\n")
    check("apply refuses a file-level error", msg is not None and "Grader" in msg, str(msg))
    msg = refused(importer.apply, c, "slabs", slabs("A,,,,X,1,,,,,,", "B,,,,Y,1,,,,,,"))
    check("plural message", msg == "Fix the 2 rows with problems and preview again", str(msg))


def test_apply_is_all_or_nothing(c):
    text = slabs("One,,,,PSA,9,71000001,5,,,,", "Two,,,,PSA,9,71000002,5,,,,",
                 "Three,,,,PSA,9,71000003,5,,,,")
    real, calls = v2cards.create_card, []

    def flaky(conn, *args, **kw):
        calls.append(kw.get("_commit"))
        if len(calls) == 2:
            raise RuntimeError("simulated failure on the 2nd card")
        return real(conn, *args, **kw)

    v2cards.create_card = flaky
    try:
        failed = False
        try:
            importer.apply(c, "slabs", text)
        except RuntimeError:
            failed = True
    finally:
        v2cards.create_card = real
    check("apply re-raises the failure", failed)
    check("apply calls create_card with _commit=False", calls == [False, False], str(calls))
    check("rollback: no cards left behind", count(c, "graded_cards") == 0)
    check("rollback: no card_events left behind", count(c, "card_events") == 0)
    check("connection is not left mid-transaction", not c.in_transaction)
    res = importer.apply(c, "slabs", text)
    check("the same file imports whole once nothing fails",
          res["created"] == 3 and count(c, "graded_cards") == 3)


def test_events_carry_source_import(c):
    importer.apply(c, "slabs", slabs("One,,,,PSA,9,81000001,5,,,,"))
    importer.apply(c, "raw", raws("Two,,,,5,,At Grading,,,"))
    srcs = {r[0] for r in c.execute("SELECT source FROM card_events")}
    n = c.execute("SELECT COUNT(*) FROM card_events WHERE field='created' AND source='import'").fetchone()[0]
    check("every event from an import is sourced 'import'", srcs == {"import"}, str(srcs))
    check("one created event per card", n == 2, str(n))
    k = v2cards.create_card(c, "graded_cards", card_name="Typed", grade="9")
    check("create_card default source is still 'you'", c.execute(
        "SELECT source FROM card_events WHERE card_id=? AND card_table='graded_cards' "
        "AND field='created'", (k,)).fetchone()[0] == "you")


TESTS = [test_valid_rows_import_with_typed_basis, test_blank_cost_is_unknown_basis,
         test_row_problems_are_reported, test_file_level_checks,
         test_raw_at_grading_matches_the_app, test_duplicate_certs,
         test_apply_refuses_when_any_row_is_bad, test_apply_is_all_or_nothing,
         test_events_carry_source_import]


def main():
    print("csv import regressions")
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
