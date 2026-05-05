#!/usr/bin/env python3
"""
Alt.xyz market pricing — test harness.

Runs the two-step Typesense → GraphQL flow for a single graded card and
prints both the raw responses (for schema inspection) and a clean pricing
summary.

SETUP
  1. Copy alt_config.example.json → alt_config.json
  2. Paste fresh tokens into alt_config.json (they expire ~5 min)
  3. Run this script

USAGE
  # CLI args:
  python3 pricing/test_harness.py --cert 12345678 --company PSA --grade 10

  # Interactive prompts (no args needed):
  python3 pricing/test_harness.py

  # Env vars instead of config file:
  ALT_TYPESENSE_KEY=xxx ALT_BEARER_TOKEN=yyy python3 pricing/test_harness.py ...
"""

import argparse
import json
import os
import sys
from pathlib import Path

# ── import alt_service from the same directory ─────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
import alt_service
from alt_service import AltServiceError, TokenExpiredError, NoResultsError

# ── config ─────────────────────────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "alt_config.json"

BAR  = "─" * 64
TICK = "✓"
WARN = "⚠"
FAIL = "✗"


def load_tokens() -> tuple[str, str]:
    """
    Load Typesense key and Bearer token.
    Priority: environment variables → alt_config.json
    """
    ts_key = os.environ.get("ALT_TYPESENSE_KEY", "").strip()
    bearer = os.environ.get("ALT_BEARER_TOKEN", "").strip()

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        ts_key = ts_key or cfg.get("typesense_api_key", "").strip()
        bearer = bearer or cfg.get("bearer_token", "").strip()

    missing = []
    if not ts_key or ts_key.startswith("PASTE_"):
        missing.append("typesense_api_key  (ALT_TYPESENSE_KEY)")
    if not bearer or bearer.startswith("PASTE_"):
        missing.append("bearer_token       (ALT_BEARER_TOKEN)")

    if missing:
        print(f"\n{FAIL} Missing credentials:")
        for m in missing:
            print(f"   {m}")
        print(f"\n  Add them to:  {CONFIG_PATH}")
        print(f"  Or set env vars: ALT_TYPESENSE_KEY, ALT_BEARER_TOKEN")
        sys.exit(1)

    return ts_key, bearer


# ── display helpers ─────────────────────────────────────────────────────────

def _fmt_price(val) -> str:
    if val is None:
        return "(not in response)"
    try:
        return f"${float(val):,.2f}"
    except (TypeError, ValueError):
        return str(val)


def _pp(label: str, data: dict | list):
    """Pretty-print a JSON block with a labelled header."""
    print(f"\n  {'─'*4} {label} {'─'*(54 - len(label))}")
    print(json.dumps(data, indent=4, default=str))


def _section(title: str):
    print(f"\n{BAR}")
    print(f"  {title}")
    print(BAR)


# ── main flow ───────────────────────────────────────────────────────────────

def run(cert_number: str, company: str, grade: str):
    ts_key, bearer = load_tokens()

    print(f"\n{BAR}")
    print(f"  ALT.XYZ PRICING TEST HARNESS")
    print(f"  Cert: {cert_number}   Company: {company}   Grade: {grade}")
    print(BAR)

    # ── Step 1: Typesense search ──────────────────────────────────────────
    _section("STEP 1 — Typesense search (cert → asset ID)")

    print(f"\n  Querying: {cert_number!r} ...")
    try:
        search_raw = alt_service.search_cert(cert_number, ts_key)
    except TokenExpiredError as e:
        print(f"\n  {FAIL} TOKEN EXPIRED:\n  {e}")
        sys.exit(1)
    except AltServiceError as e:
        print(f"\n  {FAIL} SEARCH FAILED:\n  {e}")
        sys.exit(1)

    _pp("Raw search response", search_raw)

    asset_id = alt_service.extract_asset_id(search_raw)
    if not asset_id:
        print(f"\n  {FAIL} No asset ID found in response.")
        print("  Possible causes:")
        print("    • Wrong cert number")
        print("    • Card not listed on alt.xyz")
        print("    • Expired Typesense key (try pasting a fresh one)")
        print("    • Field name differs — inspect raw response above")
        sys.exit(1)

    print(f"\n  {TICK} Asset ID: {asset_id}")

    # ── Step 2: GraphQL AssetDetails ─────────────────────────────────────
    _section("STEP 2 — GraphQL AssetDetails (asset ID → pricing)")

    print(f"\n  Querying AssetDetails for {asset_id} ...")
    try:
        details_raw = alt_service.fetch_asset_details(
            asset_id, grade, company, bearer
        )
    except TokenExpiredError as e:
        print(f"\n  {FAIL} TOKEN EXPIRED:\n  {e}")
        sys.exit(1)
    except AltServiceError as e:
        print(f"\n  {FAIL} DETAILS FETCH FAILED:\n  {e}")
        sys.exit(1)

    _pp("Raw AssetDetails response", details_raw)

    # GraphQL errors (partial success — some fields may still be present)
    if details_raw.get("errors"):
        print(f"\n  {WARN} GraphQL errors returned:")
        for err in details_raw["errors"]:
            print(f"     • {err.get('message', err)}")
        print()
        print("  One or more query fields don't match the live schema.")
        print("  Inspect the raw response above, then update ASSET_DETAILS_QUERY")
        print("  in pricing/alt_service.py to match the actual field names.")

    # ── Pricing summary ───────────────────────────────────────────────────
    pricing = alt_service.extract_pricing(details_raw)

    _section("PRICING SUMMARY")
    print()

    if pricing["asset_name"]:
        print(f"  Card:              {pricing['asset_name']}")

    print(f"  Current ALT Value: {_fmt_price(pricing['current_alt_value'])}")
    print(f"  Predicted Price:   {_fmt_price(pricing['predicted_price'])}")
    print(f"  Latest Value:      {_fmt_price(pricing['latest_value'])}")

    pops = pricing["card_pops_total"]
    print(f"  Pop (total):       {pops if pops is not None else '(not in response)'}")

    n = pricing["active_listing_count"]
    print(f"  Active Listings:   {n}")

    if pricing["active_listings"]:
        print()
        for lst in pricing["active_listings"]:
            price = lst.get("price") or lst.get("askingPrice")
            lid   = lst.get("id", "?")
            print(f"    Listing #{lid}:  {_fmt_price(price)}")

    if pricing["card_pops_breakdown"]:
        print("\n  Pop breakdown:")
        for row in pricing["card_pops_breakdown"]:
            print(f"    Grade {row.get('grade', '?'):>4}:  {row.get('count', '?')}")

    print()

    # Remind user to check if all values came back as None
    all_none = all(
        pricing[k] is None
        for k in ("current_alt_value", "predicted_price", "latest_value")
    )
    if all_none and not details_raw.get("errors"):
        print(f"  {WARN} All pricing values are None.")
        print("  The GraphQL query may have returned an empty asset node.")
        print("  Check the raw AssetDetails response above for the actual shape.")

    print(BAR)
    print()


# ── entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Test alt.xyz market pricing lookup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--cert",    metavar="CERT",
                        help="Grading cert / serial number")
    parser.add_argument("--company", metavar="CO",
                        choices=["PSA", "BGS", "CGC", "TAG"],
                        help="Grading company")
    parser.add_argument("--grade",   metavar="GRADE",
                        help="Grade value, e.g. 10 or 9.5")
    args = parser.parse_args()

    cert    = args.cert    or input("Cert number : ").strip()
    company = (args.company or input("Company (PSA/BGS/CGC/TAG) : ").strip()).upper()
    grade   = args.grade   or input("Grade (e.g. 10) : ").strip()

    errors = []
    if not cert:    errors.append("cert number is required")
    if not company: errors.append("company is required")
    if not grade:   errors.append("grade is required")
    if errors:
        for e in errors:
            print(f"  {FAIL} {e}")
        sys.exit(1)

    run(cert, company, grade)


if __name__ == "__main__":
    main()
