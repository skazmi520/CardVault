"""
alt.xyz pricing service — core fetch logic.

Two-step flow:
  1. Typesense search  →  cert number resolves to an internal asset ID
  2. GraphQL AssetDetails  →  asset ID returns pricing data

Tokens expire ~5 minutes. Load fresh ones into alt_config.json before each run.
This module is intentionally dependency-free (stdlib only) so it can be
imported anywhere in the CardVault project without adding requirements.
"""

import json
import urllib.request
import urllib.error

# ── Endpoints ─────────────────────────────────────────────────────────────────

TYPESENSE_URL = (
    "https://tlzfv6xaq81nhsbyp.a1.typesense.net"
    "/multi_search"
    "?collection=production_universal_search"
    "&use_cache=true"
    "&x-typesense-api-key={api_key}"
)

GRAPHQL_URL = (
    "https://alt-platform-server.production.internal.onlyalt.com"
    "/graphql/AssetDetails"
)

# ── GraphQL query ─────────────────────────────────────────────────────────────
# Requesting the fields mentioned in the API notes.
# If the server returns GraphQL errors for specific fields, inspect the raw
# response printed by the test harness and update the query to match.

ASSET_DETAILS_QUERY = """
query AssetDetails($id: ID!, $tsFilter: AssetTimeSeriesFilter) {
  asset(id: $id) {
    id
    name
    altValueInfo {
      currentAltValue
      predictedPrice
    }
    timeSeriesStats(filter: $tsFilter) {
      latestValue
    }
    activeListings {
      id
      price
      askingPrice
    }
    cardPops {
      total
      gradeBreakdown {
        grade
        count
      }
    }
  }
}
""".strip()


# ── Errors ────────────────────────────────────────────────────────────────────

class AltServiceError(Exception):
    """Raised for any failure in the alt.xyz fetch pipeline."""


class TokenExpiredError(AltServiceError):
    """Raised when a 401/403 suggests an expired token."""


class NoResultsError(AltServiceError):
    """Raised when a valid request returns zero hits."""


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _post(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code in (401, 403):
            raise TokenExpiredError(
                f"HTTP {e.code} — token likely expired. Paste fresh tokens into alt_config.json.\n"
                f"Response: {body[:300]}"
            ) from e
        raise AltServiceError(f"HTTP {e.code} from {url}:\n{body[:500]}") from e
    except urllib.error.URLError as e:
        raise AltServiceError(f"Network error reaching {url}: {e.reason}") from e


# ── Step 1: Typesense search ──────────────────────────────────────────────────

def search_cert(cert_number: str, typesense_key: str) -> dict:
    """
    Search Typesense for a cert number.
    Returns the full raw response dict for inspection.
    """
    url = TYPESENSE_URL.format(api_key=typesense_key)
    payload = {
        "searches": [{
            "q":         cert_number,
            "preset":    "timestamp_desc",
            "filter_by": "showResult:true",
            "per_page":  5,
            "page":      1,
        }]
    }
    return _post(url, payload, {"Content-Type": "application/json"})


def extract_asset_id(search_response: dict) -> str | None:
    """
    Pull the first asset ID from a Typesense search response.
    Returns None if no hits were found.
    Tries common field names: 'id', 'assetId', 'asset_id'.
    """
    try:
        hits = search_response["results"][0]["hits"]
    except (KeyError, IndexError, TypeError):
        return None

    if not hits:
        return None

    doc = hits[0].get("document", {})
    # Try field name variants — inspect raw response if none of these match
    for field in ("id", "assetId", "asset_id"):
        val = doc.get(field)
        if val:
            return str(val)
    return None


# ── Step 2: GraphQL AssetDetails ─────────────────────────────────────────────

def fetch_asset_details(
    asset_id:        str,
    grade_number:    str,
    grading_company: str,
    bearer_token:    str,
) -> dict:
    """
    Fetch asset pricing via GraphQL AssetDetails.
    Returns the full raw response dict for inspection.
    grade_number should be a string like "10.0" or "9.5".
    """
    headers = {
        "Authorization":    f"Bearer {bearer_token}",
        "allow-read-replica": "true",
        "Content-Type":     "application/json",
    }
    payload = {
        "operationName": "AssetDetails",
        "variables": {
            "id": asset_id,
            "tsFilter": {
                "gradeNumber":    _normalise_grade(grade_number),
                "gradingCompany": grading_company.upper(),
            },
        },
        "query": ASSET_DETAILS_QUERY,
    }
    return _post(GRAPHQL_URL, payload, headers)


def _normalise_grade(grade: str) -> str:
    """Ensure grade is a string like '10.0', '9.5', '8.0'."""
    try:
        return f"{float(grade):.1f}"
    except ValueError:
        return grade


# ── Pricing extraction ────────────────────────────────────────────────────────

def extract_pricing(details_response: dict) -> dict:
    """
    Extract key pricing fields from a raw AssetDetails response.
    Returns a flat dict; any field not present in the response is None.

    If the query doesn't match the live schema, extend ASSET_DETAILS_QUERY
    and add new keys here.
    """
    asset     = (details_response.get("data") or {}).get("asset") or {}
    alt_info  = asset.get("altValueInfo")  or {}
    ts_stats  = asset.get("timeSeriesStats") or {}
    listings  = asset.get("activeListings")  or []
    pops      = asset.get("cardPops")        or {}

    return {
        "asset_id":             asset.get("id"),
        "asset_name":           asset.get("name"),
        "current_alt_value":    alt_info.get("currentAltValue"),
        "predicted_price":      alt_info.get("predictedPrice"),
        "latest_value":         ts_stats.get("latestValue"),
        "active_listing_count": len(listings),
        "active_listings":      listings,
        "card_pops_total":      pops.get("total"),
        "card_pops_breakdown":  pops.get("gradeBreakdown"),
        # Preserve GraphQL errors so callers can surface them
        "graphql_errors":       details_response.get("errors"),
    }


# ── High-level entry point ────────────────────────────────────────────────────

def lookup_cert(
    cert_number:     str,
    grading_company: str,
    grade:           str,
    typesense_key:   str,
    bearer_token:    str,
) -> tuple[dict, dict, dict]:
    """
    Full two-step lookup.

    Returns:
        (search_raw, details_raw, pricing_summary)

    Raises:
        TokenExpiredError  — 401/403, paste fresh tokens
        NoResultsError     — cert not found on alt.xyz
        AltServiceError    — any other failure
    """
    search_raw = search_cert(cert_number, typesense_key)
    asset_id   = extract_asset_id(search_raw)

    if not asset_id:
        raise NoResultsError(
            f"No asset found for cert '{cert_number}'. "
            "Verify the cert number or check the raw search response."
        )

    details_raw = fetch_asset_details(asset_id, grade, grading_company, bearer_token)
    pricing     = extract_pricing(details_raw)
    return search_raw, details_raw, pricing
