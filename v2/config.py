"""
CardVault v2 — API key configuration.

Keys are read from (in priority order):
  1. Environment variables:      ANTHROPIC_API_KEY, PSA_API_TOKEN
  2. Local env file:             ~/.cardvaultmac/v2.env   (KEY=VALUE lines)

The env file lives OUTSIDE the git repository so keys can never be committed.
`ensure_env_file()` writes a commented template on first run.
"""

import os

from . import db as v2db

ENV_FILE = v2db.DATA_DIR / "v2.env"

_TEMPLATE = """\
# CardVault v2 — API keys (this file is outside the git repo; never committed)
#
# Anthropic API key for slab-photo extraction (pay-as-you-go account):
ANTHROPIC_API_KEY=
#
# PSA public API token (free) — register at https://www.psacard.com/publicapi
PSA_API_TOKEN=
"""


def ensure_env_file():
    if not ENV_FILE.exists():
        ENV_FILE.write_text(_TEMPLATE, encoding="utf-8")


def _read_env_file() -> dict:
    vals = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def get_keys() -> dict:
    file_vals = _read_env_file()

    def pick(name):
        return os.environ.get(name) or file_vals.get(name) or ""

    return {
        "anthropic_api_key": pick("ANTHROPIC_API_KEY"),
        "psa_api_token": pick("PSA_API_TOKEN"),
    }
