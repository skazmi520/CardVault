# CardVault v2

A local web app that supersedes the CustomTkinter desktop app (v1). v1 remains
fully intact and runnable; v2 lives alongside it with its own database.

## Running

| Method | How |
|---|---|
| Dock | double-click **CardVault v2.app** (drag to Dock; rebuild via `./v2/build_v2_app.sh`) |
| Terminal | `python3 -m v2.app` from the `CardVaultMac` folder |

Either way: **http://127.0.0.1:5177**. The launcher reuses a running server.

## Where the data lives

| | Path | Notes |
|---|---|---|
| **v2 database** | `~/.cardvaultmac/cardvault_v2.db` | all v2 reads/writes |
| v1 database | `~/.cardvaultmac/cardvault.db` | **never written by v2** — verified byte-identical after the build (sha `d216b34d…`) |
| API keys | `~/.cardvaultmac/v2.env` | `ANTHROPIC_API_KEY`, `PSA_API_TOKEN`; outside the repo |
| Slab photos | `~/.cardvaultmac/slab_photos/` | originals from the extraction pipeline |
| Deal photos | `~/.cardvaultmac/deal_photos/` | documentation attachments |
| Server log | `~/.cardvaultmac/v2_server.log` | when launched from the Dock app |

**Safety guard:** v2 refuses to open any database without the `v2_meta` marker,
so it physically cannot run against v1. The only v1 access in the codebase is
`open_v1_readonly()` (SQLite `mode=ro`).

## What v2 adds over v1

- **Deals** — every transaction (buy / sell / trade ± cash) is one Deal with
  cards-out, cards-in and signed cash. Pro-rata allocation from side totals,
  >5% reconcile warning, disposal proceeds + realized gain computed per card
  at save. Disposed cards are never deleted. Show Day running totals.
- **Collection** — dense sortable/filterable table, inline repricing with
  keyboard flow, per-card edit modal, stale-reprice flags.
- **Dashboard** — market/basis/unrealized/realized-YTD, win rate, avg profit,
  cash pool (ledger + deal flows), value-over-time chart, grader breakdown,
  top holdings, top movers (from per-reprice price history), sell-list
  candidates (85%/88% with printable sheets), at-grading tracker, attention
  panel.
- **Raw & grading** — promotion now rolls **grading cost into basis**
  (raw cost + grading fee), fixing the v1 gap.
- **Slab photos** — Haiku-vision label extraction (~$0.002/photo), PSA cert
  verification (permanently cached, 90/day budget, auto-resume), match &
  review screen (nothing writes without per-field confirmation), legacy
  backfill workflow.
- **Reports** — realized gains by year + CSV, full deal-history CSV.

## Schema additions (v1 columns preserved exactly)

`deals`, `deal_photos`, `photo_imports`, `psa_cert_cache`, `psa_budget`,
`cash_ledger`, `price_history`; card tables gain `acquired_via_deal_id`,
`disposed_via_deal_id`, `disposed_at`, `disposal_proceeds`, `realized_gain`,
`status`, (`year` on graded, `submitted_at` on raw). Migrations are idempotent
and run automatically at app start.

## Rollback

v2 never touches v1, so rollback is simply:

1. Stop the v2 server (quit the terminal / `pkill -f v2.app`).
2. Launch the old app (`CardVault.app` or `python3 main.py`) — it reads
   `cardvault.db`, which is exactly as it was.
3. Optionally delete `~/.cardvaultmac/cardvault_v2.db*`, `slab_photos/`,
   `deal_photos/`, `v2.env` to remove v2 entirely.

Anything entered **into v2** (deals, cash ledger, repricings after the
migration date) lives only in the v2 database and would need re-entry or a
reverse export if you roll back after using v2 in earnest.

## Rebuilding v2 from v1

`python3 -m v2.migrate_v1_to_v2 --force` re-copies v1 and re-applies the
schema (destroys current v2 data — the integrity report prints v1-vs-v2
checks afterwards).
