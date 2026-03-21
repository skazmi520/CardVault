# CardVault Mac

A macOS desktop app for tracking Pokemon card inventory, grading submissions, sales history, and portfolio analytics. Built with Python and customtkinter for a native-feeling macOS UI that follows system dark/light mode.

---

## Features

- **Dashboard** — portfolio stats, company breakdown, value-over-time chart, monthly P&L chart, top holdings, inventory aging, recent sales, and favorited cards
- **Inventory** — graded card collection (PSA, CGC, BGS, TAG) with photos, market values, and profit/loss tracking
- **Ungraded** — raw card tracking with grading submission workflow and grading-return conversion
- **Sold** — complete sales history with realized profit, acquisition type, and notes
- **Deal Calculator** — calculate cost basis and profit projections at any market percentage
- **Trade Evaluator** — compare multi-card trades side by side, optionally pulling cards from your live inventory

---

## Requirements

### macOS system dependencies

**Python 3.10+** is required. macOS does not ship with Python 3 by default on modern versions (Ventura and later). Install one of two ways:

**Option A — python.org installer (recommended, includes Tkinter):**
1. Download from https://www.python.org/downloads/
2. Run the `.pkg` installer — Tkinter is bundled automatically

**Option B — Homebrew:**
```bash
brew install python
brew install python-tk   # required — Tkinter is NOT included with Homebrew Python by default
```

> **Note:** If you see `ModuleNotFoundError: No module named 'tkinter'`, you are missing the Tk dependency. Use the python.org installer or run `brew install python-tk`.

### Python packages

All Python dependencies are installed automatically by `run.sh`. They are listed in `requirements.txt`:

| Package | Version | Purpose |
|---|---|---|
| `customtkinter` | ≥ 5.2.0 | Modern macOS-style UI widgets |
| `Pillow` | ≥ 10.0.0 | Card photo thumbnails |
| `matplotlib` | ≥ 3.7.0 | Portfolio and profit charts |
| `tkcalendar` | ≥ 1.6.1 | Date pickers in add/edit dialogs |

---

## Installation & Running

```bash
# Clone the repo
git clone <repo-url>
cd CardVaultMac

# Run (creates .venv and installs deps automatically)
bash run.sh
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

---

## Data Storage

All persistent data is stored in `~/.cardvaultmac/`:

```
~/.cardvaultmac/
├── cardvault.db        # SQLite database
└── photos/             # Card photo files
```

The database is created automatically on first launch. It is **not** stored in the project directory and is excluded from version control.

---

## CSV Import (one-time backfill)

`import_from_csv.py` supports importing from Google Sheets exports. Export each sheet as a separate CSV and run:

```bash
source .venv/bin/activate
python3 import_from_csv.py
```

The script will prompt for the path to each CSV file. Supported sheets:

| Sheet | Columns |
|---|---|
| PSA / CGC / BGS / TAG | Card Name, Grade, Purchase Price, Grading Fee, Current Value, Potential Profit/Loss, Total Cost |
| SOLD | Card Name, Grade, Purchase Price, Grade Fee, Total Cost, Trade Value, Cash Value, Profit/Loss |
| RAW | Card Name, Purchase Price, Current Value, Expected Grade, Graded Price, At PSA? |

- Trade Value and Cash Value are **summed** to support partial trade + partial cash deals
- "At PSA?" = Yes sets grading status to "At Grading"
- All imported records are tagged with `"Imported from Google Sheets"` in the notes field

---

## Project Structure

```
CardVaultMac/
├── main.py                  # App entry point, window, sidebar navigation
├── database.py              # SQLite schema, migrations, and all CRUD helpers
├── dashboard_view.py        # Dashboard with stats, charts, and summary sections
├── inventory_view.py        # Graded card list, add/edit/detail dialogs
├── ungraded_view.py         # Ungraded card list and grading return workflow
├── sold_view.py             # Sold history table
├── deal_calculator_view.py  # Market % cost and profit projection calculator
├── trade_evaluator_view.py  # Multi-card trade comparison tool
├── import_from_csv.py       # One-time CSV backfill from Google Sheets exports
├── requirements.txt         # Python package dependencies
└── run.sh                   # Launcher script (creates venv, installs deps, runs app)
```

---

## Portfolio Snapshots

Because market values are updated manually (no live pricing API), the "Portfolio Value Over Time" chart is powered by manual snapshots. After repricing your cards:

1. Open the Dashboard
2. Click **Save Snapshot** in the "Portfolio Value Over Time" section

Each snapshot records the date and total portfolio value. One snapshot per day is stored (re-saving the same day updates it in place).

---

## License

MIT
