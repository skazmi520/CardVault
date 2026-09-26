"""Play back deals: re-run the engine on exactly what was typed and compare
every derived number with what is stored.

    python3 tools/replay_deal.py 55        one deal
    python3 tools/replay_deal.py all       every deal that has an entry log

Read-only: opens the database with mode=ro."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from v2 import db as v2db          # noqa: E402
from v2.deals import replay        # noqa: E402


def main(arg: str) -> int:
    conn = sqlite3.connect(f"file:{v2db.V2_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ids = ([r[0] for r in conn.execute("SELECT DISTINCT deal_id FROM deal_entries ORDER BY deal_id")]
           if arg == "all" else [int(arg)])
    bad = 0
    for i in ids:
        r = replay(conn, i)
        if r["skipped"]:
            print(f"  skip   deal {i}: {r['skipped']}")
        elif r["ok"]:
            print(f"  ok     deal {i}: every derived number replays exactly")
        else:
            bad += 1
            print(f"  DIFF   deal {i}:")
            for d in r["diffs"]:
                print(f"           {d}")
    print(f"\n{len(ids)} deal(s), {bad} with differences")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "all"))
