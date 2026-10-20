# CardVault v2

A local web app that supersedes the CustomTkinter desktop app (v1).

**New here? Read the [User Guide](../USER-GUIDE.md)** — setup plus a walkthrough
of every tab. This README is the technical reference. v1 remains
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
| Deal photos | `~/.cardvaultmac/deal_photos/` | documentation attachments |
| Server log | `~/.cardvaultmac/v2_server.log` | when launched from the Dock app |

**Safety guard:** v2 refuses to open any database without the `v2_meta` marker,
so it physically cannot run against v1. The only v1 access in the codebase is
`open_v1_readonly()` (SQLite `mode=ro`).

## What v2 adds over v1

- **Deals**: every transaction (buy / sell / trade ± cash) is one Deal with
  cards-out, cards-in and signed cash. An out-card keeps its basis; its
  proceeds are the cash plus the take-ins, split by typed per-card values,
  else by market. A typed take-in is the incoming basis, verbatim; blank
  take-ins split the typed ask minus cash. Nothing booked is derived from
  market. A deal that cannot book honestly is **refused** with a reason
  (no ask for a blank take-in, cash over the ask, nothing came back, $0
  basis); there are no reconcile warnings. **Edit deal** is the
  only way a booked deal changes: the old revision is archived in
  `voided_deals` and the new one booked under the same number. Disposed
  cards are never deleted. Show Day running totals.
- **Collection**: dense sortable/filterable table, inline repricing with
  keyboard flow, per-card edit modal, stale-reprice flags.
- **Dashboard**: market/basis/unrealized over business stock (personal
  collection on its own line; a card with no market counts at cost and is
  counted), realized-YTD, win rate, avg profit,
  value-over-time chart, grader breakdown,
  top holdings, top movers (from per-reprice price history), sell-list
  candidates (85%/88% with printable sheets), at-grading tracker, attention
  panel.
- **Raw & grading**: promotion rolls **grading cost into basis** (raw cost +
  grading fee, stored as derived); an unknown raw basis stays unknown on the
  slab. Cracking carries the basis back as derived and leaves market blank.
  PSA order CSV import applies all-or-nothing in one transaction.
- **CSV import**: slabs and raw cards from a template (Collection, Import);
  previewed, strict money parsing, all-or-nothing, certs never duplicated.
- **Reports**: realized gains by year + CSV, full deal-history CSV.

## Schema (version 7)

Added tables: `deals`, `deal_photos`, `deal_entries`, `deal_lines`,
`voided_deals`, `card_events`, `photo_imports`, `psa_cert_cache`,
`psa_budget`, `cash_ledger`, `price_history` (with `source`: entered |
copied | legacy). Card tables gain deal links, disposal fields, `status`,
`is_pc` and grading fields. Migrations are idempotent and run at app start.

**Typed vs derived never share a column.** Basis lives in `basis_entered`
(typed), `basis_derived` (engine: blank take-in split, promote, crack) or
`basis_legacy` (pre-schema-7, provenance unknown). `acquisition_price` /
`purchase_price`, `basis_unknown` and `realized_gain` are GENERATED from
those, so no code can write them. A blank market stays NULL.

**Every card change is logged.** `db.update_card` / `db.record_event` write
one `card_events` row per changed field with its source (you, engine,
reprice, import, correction); the card drawer shows that history. Each deal
keeps what was typed (`deal_entries`) beside what was booked (`deal_lines`),
and `python3 tools/replay_deal.py <id|all>` re-runs the engine read-only and
diffs every derived number.

## Rollback

v2 never touches v1, so rollback is simply:

1. Stop the v2 server (quit the terminal / `pkill -f v2.app`).
2. Launch the old app (`CardVault.app` or `python3 main.py`) — it reads
   `cardvault.db`, which is exactly as it was.
3. Optionally delete `~/.cardvaultmac/cardvault_v2.db*`, `slab_photos/`,
   `deal_photos/` to remove v2 entirely.

Anything entered **into v2** (deals, cash ledger, repricings after the
migration date) lives only in the v2 database and would need re-entry or a
reverse export if you roll back after using v2 in earnest.

## Rebuilding v2 from v1

`python3 -m v2.migrate_v1_to_v2 --force` re-copies v1 and re-applies the
schema (destroys current v2 data — the integrity report prints v1-vs-v2
checks afterwards).
