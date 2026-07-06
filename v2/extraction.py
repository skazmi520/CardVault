"""
CardVault v2 — slab label extraction via the Anthropic API (claude-haiku-4-5).

- Photos are downscaled to ~1100 px on the long edge before sending
  (plenty for label text, keeps token cost down).
- The prompt lives in v2/extraction_prompt.txt so it can be tuned without
  touching code.
- Cost is computed from actual token usage (haiku: $1 / $5 per Mtok in/out).
- If no API key is configured, callers should skip extraction and fall back
  to manual entry (the UI handles this).
"""

import base64
import io
import json
import urllib.request
from pathlib import Path

from PIL import Image

MODEL = "claude-haiku-4-5"
MAX_EDGE = 1100
PROMPT_FILE = Path(__file__).parent / "extraction_prompt.txt"

# haiku pricing per token
_IN_COST = 1.0 / 1_000_000
_OUT_COST = 5.0 / 1_000_000

FIELDS = ["grading_company", "cert_number", "card_name", "set_name",
          "card_number", "year", "language", "grade", "qualifier"]


def downscale_to_jpeg_b64(path: str | Path) -> str:
    img = Image.open(path)
    img = img.convert("RGB")
    w, h = img.size
    scale = MAX_EDGE / max(w, h)
    if scale < 1:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode()


def extract_label(image_path: str | Path, api_key: str) -> dict:
    """Run the label through Haiku. Returns
    {fields: {...}, low_confidence: [...], cost: float, raw: str}
    Raises RuntimeError on API/parse failure."""
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY configured")

    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    payload = {
        "model": MODEL,
        "max_tokens": 500,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg",
                            "data": downscale_to_jpeg_b64(image_path)}},
                {"type": "text", "text": prompt},
            ],
        }],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"x-api-key": api_key,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"Anthropic API {e.code}: {body}") from e

    text = "".join(b.get("text", "") for b in resp.get("content", []))
    usage = resp.get("usage", {})
    cost = round(usage.get("input_tokens", 0) * _IN_COST
                 + usage.get("output_tokens", 0) * _OUT_COST, 6)

    # strict-JSON parse (strip accidental fences defensively)
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").lstrip("json").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Model did not return valid JSON: {text[:200]}") from e

    fields = {k: str(data.get(k, "") or "").strip() for k in FIELDS}
    low = [f for f in data.get("low_confidence", []) if f in FIELDS]
    return {"fields": fields, "low_confidence": low, "cost": cost, "raw": text}
