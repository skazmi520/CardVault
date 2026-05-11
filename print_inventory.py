"""
CardVault Mac — Printable stock check list generator.

Generates a self-contained HTML file and opens it in the default browser.
The browser's Cmd+P → "Save as PDF" handles both print and PDF export
with no extra dependencies.

Layout mirrors the Stock Check view: checkbox | grade badge | card name | card #
No financial data is included.
"""

import tempfile
import webbrowser
from datetime import date
import database as db

BADGE_CSS = {
    "PSA": "#FF3B30",
    "BGS": "#007AFF",
    "CGC": "#AF52DE",
    "TAG": "#5AC8FA",
}


def _badge(company: str, grade: str) -> str:
    color = BADGE_CSS.get(company, "#555")
    label = f"{company} {grade}".strip()
    return f'<span class="badge" style="background:{color}">{label}</span>'


def _checkbox() -> str:
    return '<span class="cb"></span>'


def generate_html(cards: list, title: str = "CardVault — Stock Check") -> str:
    today = date.today().strftime("%B %d, %Y")
    total = len(cards)

    # Sort: company then card name (matches Stock Check view)
    cards = sorted(cards, key=lambda c: (
        c["grading_company"] or "",
        (c["card_name"] or "").lower(),
    ))

    rows_html = ""
    for i, c in enumerate(cards):
        company    = c["grading_company"] or ""
        grade      = c["grade"] or ""
        card_name  = c["card_name"] or ""
        card_num   = f"#{c['card_number']}" if c["card_number"] else ""
        set_name   = c["set_name"] or ""
        row_class  = "even" if i % 2 == 0 else ""

        rows_html += f"""
        <tr class="{row_class}">
          <td class="cb-cell">{_checkbox()}</td>
          <td class="num-cell">{i + 1}</td>
          <td class="badge-cell">{_badge(company, grade)}</td>
          <td class="name-cell"><strong>{card_name}</strong></td>
          <td class="set-cell">{set_name}</td>
          <td class="cardnum-cell">{card_num}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
      font-size: 11px;
      color: #1a1a1a;
      padding: 24px 28px;
    }}

    /* ── Header ───────────────────────────────── */
    .page-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 18px;
      padding-bottom: 12px;
      border-bottom: 2px solid #1a1a1a;
    }}
    h1 {{ font-size: 20px; font-weight: 700; }}
    .sub {{
      font-size: 10px;
      color: #666;
      margin-top: 3px;
    }}

    /* ── Print tip ────────────────────────────── */
    .print-tip {{
      font-size: 11px;
      color: #555;
      text-align: right;
      line-height: 1.8;
    }}

    /* ── Table ────────────────────────────────── */
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th {{
      background: #1a1a1a;
      color: #fff;
      padding: 6px 8px;
      text-align: left;
      font-size: 8px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    td {{
      padding: 6px 8px;
      border-bottom: 1px solid #efefef;
      vertical-align: middle;
      font-size: 10px;
    }}
    tr.even td {{ background: #fafafa; }}

    /* ── Column widths ────────────────────────── */
    .cb-cell      {{ width: 30px; text-align: center; }}
    .num-cell     {{ width: 30px; color: #aaa; font-size: 9px; }}
    .badge-cell   {{ width: 82px; }}
    .name-cell    {{ }}  /* flex */
    .set-cell     {{ width: 140px; color: #555; }}
    .cardnum-cell {{ width: 64px; color: #888; text-align: right; font-family: monospace; }}

    /* ── Checkbox square ──────────────────────── */
    .cb {{
      display: inline-block;
      width: 14px;
      height: 14px;
      border: 1.5px solid #888;
      border-radius: 2px;
      vertical-align: middle;
    }}

    /* ── Badge ────────────────────────────────── */
    .badge {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 3px;
      color: #fff;
      font-weight: 700;
      font-size: 9px;
      letter-spacing: 0.2px;
      white-space: nowrap;
    }}

    /* ── Print overrides ──────────────────────── */
    @media print {{
      body {{ padding: 0; }}
      @page {{ margin: 1.2cm 1.5cm; size: letter portrait; }}
      .no-print {{ display: none; }}
      tr.even td {{ background: #fafafa !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .badge {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    }}
  </style>
</head>
<body>

  <div class="page-header">
    <div>
      <h1>CardVault — Stock Check</h1>
      <div class="sub">Generated {today} &nbsp;·&nbsp; {total} card{'' if total == 1 else 's'}</div>
    </div>
    <div class="print-tip no-print">
      Press <strong>Cmd+P</strong> to print or save as PDF<br>
      Portrait orientation recommended
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th class="cb-cell">✓</th>
        <th class="num-cell">#</th>
        <th class="badge-cell">Grade</th>
        <th class="name-cell">Card Name</th>
        <th class="set-cell">Set</th>
        <th class="cardnum-cell">Card #</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>

</body>
</html>"""


def open_print_view():
    """Pull live inventory from the database, generate HTML, open in browser."""
    cards = db.get_graded_cards(sold=False)
    html  = generate_html(cards)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix="cardvault_stockcheck_",
        delete=False, encoding="utf-8",
    )
    tmp.write(html)
    tmp.close()

    webbrowser.open(f"file://{tmp.name}")
