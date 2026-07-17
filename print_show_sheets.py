"""
CardVault Mac — Printable show sheets.

Generates three browser-ready HTML sheets for use at a card show:

  1. Full inventory sell sheet  — name, number, cost basis, current price,
     blank Sold Price column, plus blank write-in rows for new acquisitions.
  2. Sell-at-88% profit sheet   — cards still profitable at 88% of market.
  3. Sell-at-85% profit sheet   — cards still profitable at 85% of market.

Each opens in the default browser; use Cmd+P → Save as PDF / print.
"""

import webbrowser
from datetime import date
from pathlib import Path
import database as db

OUT_DIR = Path.home() / "Desktop" / "CardVault Show Sheets"

BADGE_CSS = {
    "PSA": "#FF3B30",
    "BGS": "#007AFF",
    "CGC": "#AF52DE",
    "TAG": "#5AC8FA",
}

WRITE_IN_ROWS = 18   # blank rows for new acquisitions on the full sheet


def _fmt(val) -> str:
    if val is None or val == 0:
        return ""
    try:
        return f"${float(val):,.2f}"
    except (TypeError, ValueError):
        return ""


def _badge(company: str, grade: str) -> str:
    color = BADGE_CSS.get(company, "#555")
    label = f"{company} {grade}".strip()
    return f'<span class="badge" style="background:{color}">{label}</span>'


# ── shared page shell ──────────────────────────────────────────────────────────

def _page(title: str, subtitle: str, body: str) -> str:
    today = date.today().strftime("%B %d, %Y")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
      font-size: 11px; color: #1a1a1a; padding: 24px 28px;
    }}
    .page-header {{
      display: flex; justify-content: space-between; align-items: flex-start;
      margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid #1a1a1a;
    }}
    h1 {{ font-size: 19px; font-weight: 700; }}
    .sub {{ font-size: 10px; color: #666; margin-top: 3px; }}
    .print-tip {{ font-size: 11px; color: #555; text-align: right; line-height: 1.8; }}

    table {{ width: 100%; border-collapse: collapse; }}
    th {{
      background: #1a1a1a; color: #fff; padding: 6px 8px; text-align: left;
      font-size: 8px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px;
    }}
    td {{ padding: 6px 8px; border-bottom: 1px solid #eee; vertical-align: middle; font-size: 10px; }}
    tr.even td {{ background: #fafafa; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .idx {{ width: 30px; color: #aaa; font-size: 9px; }}
    .badge {{
      display: inline-block; padding: 2px 7px; border-radius: 3px; color: #fff;
      font-weight: 700; font-size: 9px; white-space: nowrap;
    }}
    .blank-cell {{ border-bottom: 1px solid #bbb; height: 22px; }}
    .write-row td {{ height: 30px; }}
    .section-title {{
      font-size: 13px; font-weight: 700; margin: 22px 0 8px;
      padding-bottom: 4px; border-bottom: 1px solid #ccc;
    }}
    .profit  {{ color: #1a7f37; font-weight: 600; }}
    .totals td {{ border-top: 2px solid #bbb; font-weight: 700; background: #f0f0f0; padding: 7px 8px; }}

    @media print {{
      body {{ padding: 0; }}
      @page {{ margin: 1.1cm 1.3cm; size: letter portrait; }}
      .no-print {{ display: none; }}
      tr.even td {{ background: #fafafa !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .badge, .totals td {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    }}
  </style>
</head>
<body>
  <div class="page-header">
    <div>
      <h1>{title}</h1>
      <div class="sub">{subtitle} &nbsp;·&nbsp; Generated {today}</div>
    </div>
    <div class="print-tip no-print">
      Press <strong>Cmd+P</strong> to print or save as PDF
    </div>
  </div>
  {body}
</body>
</html>"""


# ── 1. full inventory sell sheet ────────────────────────────────────────────────

def generate_full_inventory(cards) -> str:
    cards = sorted(cards, key=lambda c: (c["grading_company"] or "", (c["card_name"] or "").lower()))

    rows = ""
    total_cost = total_mkt = 0.0
    for i, c in enumerate(cards):
        cost = c["acquisition_price"] or 0
        mkt  = c["market_value"] or 0
        total_cost += cost
        total_mkt  += mkt
        rows += f"""
        <tr class="{'even' if i % 2 == 0 else ''}">
          <td class="idx">{i + 1}</td>
          <td>{_badge(c['grading_company'] or '', c['grade'] or '')}</td>
          <td><strong>{c['card_name'] or ''}</strong></td>
          <td>{('#' + c['card_number']) if c['card_number'] else ''}</td>
          <td class="num">{_fmt(cost)}</td>
          <td class="num">{_fmt(mkt)}</td>
          <td class="blank-cell"></td>
        </tr>"""

    totals = f"""
        <tr class="totals">
          <td colspan="4" style="text-align:right">TOTALS ({len(cards)} cards)</td>
          <td class="num">{_fmt(total_cost)}</td>
          <td class="num">{_fmt(total_mkt)}</td>
          <td></td>
        </tr>"""

    write_in = "".join(
        f"""
        <tr class="write-row {'even' if i % 2 == 0 else ''}">
          <td class="idx"></td>
          <td class="blank-cell"></td>
          <td class="blank-cell"></td>
          <td class="num blank-cell"></td>
        </tr>""" for i in range(WRITE_IN_ROWS)
    )

    body = f"""
    <table>
      <thead>
        <tr>
          <th class="idx">#</th>
          <th style="width:78px">Grade</th>
          <th>Card Name</th>
          <th style="width:64px">Card #</th>
          <th class="num" style="width:90px">Cost Basis</th>
          <th class="num" style="width:90px">Current Price</th>
          <th style="width:110px">Sold Price</th>
        </tr>
      </thead>
      <tbody>{rows}{totals}</tbody>
    </table>

    <div class="section-title">New Acquisitions (write-in)</div>
    <table>
      <thead>
        <tr>
          <th class="idx">#</th>
          <th>Card Name</th>
          <th>Card #</th>
          <th class="num" style="width:120px">Price Paid</th>
        </tr>
      </thead>
      <tbody>{write_in}</tbody>
    </table>"""

    return _page("CardVault — Full Inventory", f"{len(cards)} cards · sell sheet", body)


# ── 2 & 3. threshold profit sheets ──────────────────────────────────────────────

def generate_threshold(cards, pct: int) -> str:
    ratio = pct / 100.0
    rows_data = []
    for c in cards:
        cost = c["acquisition_price"] or 0
        mkt  = c["market_value"] or 0
        if mkt <= 0:
            continue
        sale   = mkt * ratio
        profit = sale - cost
        if profit > 0:
            rows_data.append((c, cost, mkt, sale, profit))

    rows_data.sort(key=lambda x: x[4], reverse=True)

    rows = ""
    tot_cost = tot_sale = tot_profit = 0.0
    for i, (c, cost, mkt, sale, profit) in enumerate(rows_data):
        tot_cost += cost; tot_sale += sale; tot_profit += profit
        rows += f"""
        <tr class="{'even' if i % 2 == 0 else ''}">
          <td class="idx">{i + 1}</td>
          <td>{_badge(c['grading_company'] or '', c['grade'] or '')}</td>
          <td><strong>{c['card_name'] or ''}</strong></td>
          <td>{('#' + c['card_number']) if c['card_number'] else ''}</td>
          <td class="num">{_fmt(cost)}</td>
          <td class="num">{_fmt(mkt)}</td>
          <td class="num">{_fmt(sale)}</td>
          <td class="num profit">{_fmt(profit)}</td>
        </tr>"""

    totals = f"""
        <tr class="totals">
          <td colspan="4" style="text-align:right">TOTALS ({len(rows_data)} cards)</td>
          <td class="num">{_fmt(tot_cost)}</td>
          <td></td>
          <td class="num">{_fmt(tot_sale)}</td>
          <td class="num">{_fmt(tot_profit)}</td>
        </tr>""" if rows_data else ""

    body = f"""
    <table>
      <thead>
        <tr>
          <th class="idx">#</th>
          <th style="width:78px">Grade</th>
          <th>Card Name</th>
          <th style="width:64px">Card #</th>
          <th class="num" style="width:85px">Cost Basis</th>
          <th class="num" style="width:90px">Current Price</th>
          <th class="num" style="width:90px">{pct}% Price</th>
          <th class="num" style="width:85px">Profit</th>
        </tr>
      </thead>
      <tbody>{rows}{totals}</tbody>
    </table>"""

    return _page(f"CardVault — Sell at {pct}%",
                 f"{len(rows_data)} cards profitable at {pct}% of market", body)


# ── runner ──────────────────────────────────────────────────────────────────────

def run(open_browser: bool = True) -> list[Path]:
    cards = db.get_graded_cards(sold=False)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    files = {
        "Full Inventory.html":   generate_full_inventory(cards),
        "Sell at 88 percent.html": generate_threshold(cards, 88),
        "Sell at 85 percent.html": generate_threshold(cards, 85),
    }

    paths = []
    for name, html in files.items():
        p = OUT_DIR / name
        p.write_text(html, encoding="utf-8")
        paths.append(p)
        if open_browser:
            webbrowser.open(f"file://{p}")

    return paths


def _v1_staleness_warning():
    """v1 reads cardvault.db. CardVault v2 has its own database, so anything
    generated here is frozen at whatever v1 last knew — no deals, no basis
    corrections, none of the cards added since. Refuse to run silently."""
    import sys
    from pathlib import Path
    if not (Path.home() / ".cardvaultmac" / "cardvault_v2.db").exists():
        return
    print("=" * 72)
    print("WARNING: this is the v1 generator and reads the v1 database.")
    print("A v2 database exists, so this output will be STALE — it will not")
    print("include deals, corrected cost bases, or any card added in v2.")
    print("")
    print("Use v2 instead (python3 -m v2.app):")
    print("    Reports -> Printable sheet      (full inventory + write-in rows)")
    print("    Reports -> Sell List            (any % of market)")
    print("    Stock Check -> Print list       (physical count sheet)")
    print("=" * 72)
    if input("Generate stale v1 sheets anyway? [y/N] ").strip().lower() != "y":
        sys.exit(1)

if __name__ == "__main__":
    _v1_staleness_warning()
    for p in run(open_browser=False):
        print(p)
