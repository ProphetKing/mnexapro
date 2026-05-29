"""Resolve a GitHub auth token.

Tried in order:
  1. `gh auth token` from the GitHub CLI (zero setup if you already use `gh`)
  2. `GITHUB_TOKEN` environment variable (or `.env`)
  3. Anonymous (None) — works for public repos with reduced rate limits

The token is used as `Authorization: Bearer <token>`.
"""

from __future__ import annotations

import os
import subprocess


def get_token() -> str | None:
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0:
        token = result.stdout.strip()
        if token:
            return token
    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token:
        return env_token.strip()
    return None
