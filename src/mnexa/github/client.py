# pyright: basic
"""REST client for GitHub's content API.

Spec: https://docs.github.com/en/rest/repos/contents

Endpoints used:
    GET /repos/{owner}/{repo}                  — repo metadata (default branch)
    GET /repos/{owner}/{repo}/contents         — top-level directory listing
    GET /repos/{owner}/{repo}/contents/{path}  — single file metadata + content

Auth: optional `Authorization: Bearer <token>`. Public repos work
anonymously with a 60-req/hr rate limit; authenticated calls get 5000/hr.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

API_BASE = "https://api.github.com"


@dataclass
class GitHubFile:
    owner: str
    repo: str
    branch: str
    path: str           # relative to repo root, e.g. "README.md"
    blob_sha: str       # git's SHA-1 of the file content; the sync key
    html_url: str       # https://github.com/<owner>/<repo>/blob/<branch>/<path>
    size: int


class GitHubClient:
    def __init__(self, token: str | None) -> None:
        import httpx

        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mnexa",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=API_BASE, headers=headers, timeout=30.0,
        )

    def default_branch(self, owner: str, repo: str) -> str:
        resp = self._client.get(f"/repos/{owner}/{repo}")
        resp.raise_for_status()
        return resp.json().get("default_branch", "main")

    def list_top_level_md(self, owner: str, repo: str, branch: str) -> list[GitHubFile]:
        """List top-level *.md files at the given ref. One API call."""
        resp = self._client.get(
            f"/repos/{owner}/{repo}/contents", params={"ref": branch},
        )
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            return []
        return [
            _to_file(item, owner, repo, branch)
            for item in items
            if item.get("type") == "file"
            and item.get("name", "").lower().endswith(".md")
        ]

    def get_file(
        self, owner: str, repo: str, path: str, branch: str,
    ) -> tuple[bytes, GitHubFile]:
        """Fetch one file's content + metadata. Content comes back base64-encoded."""
        resp = self._client.get(
            f"/repos/{owner}/{repo}/contents/{path}", params={"ref": branch},
        )
        resp.raise_for_status()
        meta = resp.json()
        if isinstance(meta, list):
            raise ValueError(f"{path} is a directory, not a file")
        if meta.get("type") != "file":
            raise ValueError(f"{path} is type={meta.get('type')!r}, not file")
        encoding = meta.get("encoding", "base64")
        content_str = meta.get("content", "")
        if encoding == "base64":
            content = base64.b64decode(content_str)
        else:
            content = content_str.encode("utf-8")
        return content, _to_file(meta, owner, repo, branch)

    def close(self) -> None:
        self._client.close()


def _to_file(raw: dict[str, Any], owner: str, repo: str, branch: str) -> GitHubFile:
    return GitHubFile(
        owner=owner,
        repo=repo,
        branch=branch,
        path=raw.get("path") or raw.get("name") or "",
        blob_sha=raw.get("sha") or "",
        html_url=raw.get("html_url") or "",
        size=int(raw.get("size") or 0),
    )
