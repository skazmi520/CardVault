# CardVault

Track a Pokémon card collection like a portfolio: inventory, deals (buy / sell /
multi-card trades with cash), raw-card grading workflow, slab-photo recognition,
repricing, and realized-gains reporting.

**CardVault v2** is a local web app (Flask + a dark, Collectr-style UI) that runs
on your Mac and is usable from your phone over Tailscale. The original desktop
app (**v1**, CustomTkinter) remains in the repo as legacy.

---

## Install (v2 — fresh install, no v1 required)

**Requirement:** Python 3.10+ (`brew install python` or [python.org](https://www.python.org/downloads/)).

```bash
git clone <repo-url>
cd CardVaultMac
./v2/install.sh
```

The installer:
1. installs the Python dependencies (`flask`, `Pillow`)
2. creates the v2 database at `~/.cardvaultmac/cardvault_v2.db`
   - **fresh machine** → a new empty database
   - **existing v1 database found** → migrates your v1 data into v2
     (v1 is opened read-only and never modified)
3. builds **CardVault v2.app** — drag it to your Dock

Run it:

```bash
python3 -m v2.app          # → http://127.0.0.1:5177
# or double-click "CardVault v2.app"
```

## v2 features

- **Dashboard** — market value, cost basis, unrealized & realized YTD gains,
  cash pool with ledger, value-over-time chart, grader breakdown, top holdings,
  top movers, sell-list candidates, at-grading tracker
- **Collection** — dense sortable table over slabs + raw, search/filters,
  inline market-value repricing with keyboard flow, per-card edit modal
- **Deals** — every transaction is one deal: cards out, cards in, signed cash;
  pro-rata allocation, reconcile warnings, per-card realized gains; Show Day
  running totals for shows
- **Raw & Grading** — grading pipeline; promotion rolls grading cost into basis
- **Slab Photos** — batch label extraction via the Anthropic API
  (claude-haiku-4-5 vision, <1¢/slab), PSA cert verification (free public API,
  permanently cached), review screen with per-field accept, legacy backfill mode
- **Reports** — realized gains by year, sold-card search, CSV exports

### Optional API keys

Put keys in `~/.cardvaultmac/v2.env` (created on first run, never committed):

```
ANTHROPIC_API_KEY=   # slab photo extraction (pay-as-you-go)
PSA_API_TOKEN=       # free — register at psacard.com/publicapi
CARDVAULT_HOST=      # set to 0.0.0.0 to use from your phone via Tailscale
```

Without keys, photo features degrade to manual entry; everything else works.

### Data locations

```
~/.cardvaultmac/
├── cardvault_v2.db     # v2 database (all v2 data)
├── cardvault.db        # v1 database (legacy — never written by v2)
├── v2.env              # API keys / config
├── slab_photos/        # slab label photos
├── deal_photos/        # deal documentation photos
├── photos/             # v1 card photos
└── backups/            # v1 daily local backups
```

See [v2/README.md](v2/README.md) for architecture, schema, and rollback details.

---

## v1 (legacy desktop app)

The original CustomTkinter desktop app. Not required for v2 — kept for
reference and rollback.

<details>
<summary>v1 install &amp; usage</summary>

Requires Tkinter (`brew install python-tk` if using Homebrew Python).

```bash
bash run.sh          # creates .venv, installs deps, launches
```

v1 stores data in `~/.cardvaultmac/cardvault.db` with daily backups to
`~/.cardvaultmac/backups/` and iCloud Drive.

**CSV import** (one-time Google Sheets backfill): `python3 import_from_csv.py`
with PSA/CGC/BGS/TAG/SOLD/RAW sheet exports.

</details>

---

## License

MIT
