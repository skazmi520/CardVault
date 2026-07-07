"""
CardVault v2 — PSA cert verification pass over active graded cards.

Run:  python3 -m v2.cert_backfill

For every active PSA card with a cert number:
  - looks the cert up (permanent cache first, then the API within the daily
    budget, paced to respect PSA's short-window throttle)
  - fills year / card number ONLY where blank
  - reports (never overwrites) disagreements on year, card number and grade

Safe to re-run any time: cached certs cost nothing, and the pass stops
cleanly when throttled or out of budget — just run it again later.
"""

import sys
import time

from . import config
from . import db as v2db
from . import psa_api


def main():
    conn = v2db.get_connection()
    tok = config.get_keys()["psa_api_token"]
    cards = conn.execute(
        "SELECT * FROM graded_cards WHERE status='active' AND grading_company='PSA' "
        "AND TRIM(COALESCE(serial_number,'')) != '' ORDER BY id").fetchall()
    print(f"{len(cards)} PSA cert cards | budget left today: {psa_api.budget_remaining(conn)}")

    applied, checked, flags = 0, 0, []
    for c in cards:
        res = psa_api.lookup_cert(conn, c["serial_number"], tok)
        st = res["status"]
        if st in ("rate_limited", "queued"):
            print(f"\nstopping: {res.get('error', st)} — re-run later, progress is saved")
            break
        if st in ("not_found", "error", "no_token"):
            flags.append(f"id{c['id']} {c['card_name']}: cert {c['serial_number']} → {st} "
                         f"{res.get('error', '')}")
            if st in ("error", "no_token"):
                break
            continue

        d = res["data"]
        checked += 1
        updates = {}
        if d.get("year") and not (c["year"] or "").strip():
            updates["year"] = d["year"]
        if d.get("card_number") and not (c["card_number"] or "").strip():
            updates["card_number"] = d["card_number"]
        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE graded_cards SET {sets} WHERE id=?",
                         [*updates.values(), c["id"]])
            conn.commit()
            applied += 1
            print(f"  id{c['id']:<4} {c['card_name'][:34]:<34} filled {updates}")

        for field, ours, theirs in [
                ("year", (c["year"] or "").strip(), d.get("year", "")),
                ("card #", (c["card_number"] or "").lstrip("0"),
                 (d.get("card_number") or "").lstrip("0")),
                ("grade", str(c["grade"]).strip(), d.get("grade", ""))]:
            if ours and theirs and ours != theirs:
                flags.append(f"id{c['id']} {c['card_name']}: {field} ours '{ours}' "
                             f"vs PSA '{theirs}'")

        if st == "fetched":
            time.sleep(2)

    print(f"\nchecked {checked} certs | filled fields on {applied} cards "
          f"| budget used today: {psa_api.budget_used(conn)}")
    if flags:
        print("\nDISCREPANCIES (review, nothing overwritten):")
        for f in flags:
            print(" ", f)
    conn.close()


if __name__ == "__main__":
    main()
