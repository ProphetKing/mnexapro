"""Bearer-token auth for Granola.

Granola's REST API uses static API keys (`Authorization: Bearer <key>`).
Personal API keys are available on Business / Enterprise plans only — that
is a Granola-side limitation, not a mnexa one.

Reads `GRANOLA_API_KEY` from the environment (or `.env`, loaded by the CLI).
"""

from __future__ import annotations

import os


def get_api_key() -> str:
    key = os.environ.get("GRANOLA_API_KEY")
    if not key:
        raise RuntimeError(
            "GRANOLA_API_KEY is not set. Create a personal API key at "
            "https://app.granola.ai (Business or Enterprise plan required) "
            "and put it in your .env."
        )
    return key
