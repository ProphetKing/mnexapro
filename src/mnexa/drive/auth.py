# pyright: basic
"""Google OAuth loopback flow + token cache.

Reads `MNEXA_GOOGLE_CLIENT_ID` and `MNEXA_GOOGLE_CLIENT_SECRET` from env
(or .env). For desktop OAuth clients these "secrets" are public per
Google's spec; the env-var indirection keeps the published mnexa source
free of project-specific values until the user sets up their GCP project.

Token cache lives at `~/.config/mnexa/google-token.json`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
TOKEN_PATH = Path.home() / ".config" / "mnexa" / "google-token.json"


def get_credentials() -> Any:
    """Return a refreshed `google.oauth2.credentials.Credentials`.

    On first run (or after token expiry that can't be refreshed), opens a
    browser for the OAuth loopback flow. Subsequent runs reuse the cached
    refresh token silently.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds: Credentials | None = None
    if TOKEN_PATH.is_file():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)
        return creds

    client_config = _client_config_from_env()
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)  # pyright: ignore[reportAssignmentType]
    _save_token(creds)
    return creds


def _client_config_from_env() -> dict[str, dict[str, Any]]:
    cid = os.environ.get("MNEXA_GOOGLE_CLIENT_ID")
    csec = os.environ.get("MNEXA_GOOGLE_CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError(
            "Google Drive integration requires MNEXA_GOOGLE_CLIENT_ID and "
            "MNEXA_GOOGLE_CLIENT_SECRET environment variables. Create a "
            "Desktop OAuth Client in a Google Cloud project (with the Drive "
            "API enabled) and set both values in your .env. See README."
        )
    return {
        "installed": {
            "client_id": cid,
            "client_secret": csec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def _save_token(creds: Any) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")


def revoke() -> None:
    """Delete the cached token. The user can then re-auth on the next run."""
    if TOKEN_PATH.is_file():
        TOKEN_PATH.unlink()


def cached_token_info() -> dict[str, Any] | None:
    if not TOKEN_PATH.is_file():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
