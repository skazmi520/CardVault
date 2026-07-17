"""
CardVault v2 — slab label extraction via the Anthropic API.

- Model: claude-opus-4-8 with high-resolution vision (2576 px long edge).
  Cert numbers are small print — resolution and model strength are what
  make digit-level transcription reliable.
- Photos are EXIF-corrected and downscaled to <= 2576 px before sending.
- Sideways photos (label text rotated 90°) are common; if the extracted
  cert number is implausible, the image is retried rotated 90° CW then CCW
  and the first plausible result wins. Cost of all attempts is summed.
- The prompt lives in v2/extraction_prompt.txt so it can be tuned without
  touching code.
- Cost is computed from actual token usage (opus: $5 / $25 per Mtok in/out).
- If no API key is configured, callers should skip extraction and fall back
  to manual entry (the UI handles this).
"""

import base64
import io
import json
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

MODEL = "claude-opus-4-8"
MAX_EDGE = 2576
PROMPT_FILE = Path(__file__).parent / "extraction_prompt.txt"

# opus pricing per token
_IN_COST = 5.0 / 1_000_000
_OUT_COST = 25.0 / 1_000_000

FIELDS = ["grading_company", "cert_number", "card_name", "set_name",
          "card_number", "year", "language", "grade", "qualifier"]


def prepare_jpeg_b64(path: str | Path, rotate: int = 0) -> str:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    if rotate:
        img = img.rotate(rotate, expand=True)
    w, h = img.size
    scale = MAX_EDGE / max(w, h)
    if scale < 1:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode()


def _cert_plausible(fields: dict) -> bool:
    """Grader certs are 7-10 digit numbers; anything else is a misread."""
    digits = "".join(ch for ch in str(fields.get("cert_number", "")) if ch.isdigit())
    return 7 <= len(digits) <= 10


def _attempt(image_path: str | Path, api_key: str, rotate: int) -> dict:
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    payload = {
        "model": MODEL,
        "max_tokens": 700,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg",
                            "data": prepare_jpeg_b64(image_path, rotate)}},
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
        with urllib.request.urlopen(req, timeout=120) as r:
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


def extract_label(image_path: str | Path, api_key: str) -> dict:
    """Run the label through the model, retrying rotated if the cert number
    comes back implausible. Returns
    {fields: {...}, low_confidence: [...], cost: float, raw: str}
    Raises RuntimeError on API/parse failure."""
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY configured")

    total_cost = 0.0
    best = None
    # 0 = as taken; 270 = 90° clockwise; 90 = 90° counter-clockwise
    for rotate in (0, 270, 90):
        result = _attempt(image_path, api_key, rotate)
        total_cost += result["cost"]
        if best is None:
            best = result
        if _cert_plausible(result["fields"]) and \
                "cert_number" not in result["low_confidence"]:
            best = result
            break
    best["cost"] = round(total_cost, 6)
    return best
