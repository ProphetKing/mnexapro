# pyright: basic
"""REST client for Granola's public API.

Spec: https://docs.granola.ai/api-reference/openapi.json
Endpoints used:
    GET /v1/notes              — list (paginated, with created_after / updated_after)
    GET /v1/notes/{note_id}    — single note (with ?include=transcript)

Auth: `Authorization: Bearer grn_<key>`. Personal API keys require a
Granola Business or Enterprise plan (Granola-side limitation).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

API_BASE = "https://public-api.granola.ai/v1"


@dataclass
class GranolaUser:
    name: str | None
    email: str

    @property
    def display(self) -> str:
        return self.name or self.email


@dataclass
class GranolaNoteSummary:
    """Shape of an item in `GET /v1/notes` response."""
    note_id: str
    title: str
    created_at: str
    updated_at: str
    owner: GranolaUser


@dataclass
class GranolaNote:
    """Shape of `GET /v1/notes/{id}?include=transcript` response."""
    note_id: str
    title: str
    created_at: str
    updated_at: str
    owner: GranolaUser
    web_url: str
    summary_text: str
    summary_markdown: str | None
    transcript: list[dict[str, Any]]
    attendees: list[GranolaUser]
    folder_names: list[str]
    raw: dict[str, Any]  # everything else, in case fields are added


class GranolaClient:
    def __init__(self, api_key: str) -> None:
        import httpx

        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    def list_notes(
        self,
        *,
        created_after: str | None = None,
        updated_after: str | None = None,
        page_size: int = 30,
    ) -> Iterator[GranolaNoteSummary]:
        """Yield all notes, paginating via `cursor` until `hasMore` is false.

        `page_size` is capped at 30 server-side (default 10).
        """
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": min(page_size, 30)}
            if cursor:
                params["cursor"] = cursor
            if created_after:
                params["created_after"] = created_after
            if updated_after:
                params["updated_after"] = updated_after
            resp = self._client.get("/notes", params=params)
            resp.raise_for_status()
            data = resp.json()
            for raw in data.get("notes", []):
                yield _to_summary(raw)
            if not data.get("hasMore"):
                break
            cursor = data.get("cursor")
            if not cursor:
                break

    def get_note(self, note_id: str) -> GranolaNote:
        resp = self._client.get(
            f"/notes/{note_id}", params={"include": "transcript"},
        )
        resp.raise_for_status()
        return _to_note(resp.json())

    def close(self) -> None:
        self._client.close()


def _to_user(raw: dict[str, Any] | None) -> GranolaUser:
    raw = raw or {}
    return GranolaUser(name=raw.get("name"), email=raw.get("email") or "")


def _to_summary(raw: dict[str, Any]) -> GranolaNoteSummary:
    return GranolaNoteSummary(
        note_id=raw["id"],
        title=raw.get("title") or "(untitled)",
        created_at=raw.get("created_at") or "",
        updated_at=raw.get("updated_at") or "",
        owner=_to_user(raw.get("owner")),
    )


def _to_note(raw: dict[str, Any]) -> GranolaNote:
    folder_names = [
        f.get("name", "") for f in (raw.get("folder_membership") or [])
        if isinstance(f, dict)
    ]
    attendees = [_to_user(a) for a in (raw.get("attendees") or [])]
    return GranolaNote(
        note_id=raw["id"],
        title=raw.get("title") or "(untitled)",
        created_at=raw.get("created_at") or "",
        updated_at=raw.get("updated_at") or "",
        owner=_to_user(raw.get("owner")),
        web_url=raw.get("web_url") or "",
        summary_text=raw.get("summary_text") or "",
        summary_markdown=raw.get("summary_markdown"),
        transcript=raw.get("transcript") or [],
        attendees=attendees,
        folder_names=folder_names,
        raw=raw,
    )


def render_note_text(note: GranolaNote) -> str:
    """Flatten a GranolaNote into plain text for the LLM."""
    parts: list[str] = []
    parts.append(f"# {note.title}\n")

    if note.owner.email:
        parts.append(f"Owner: {note.owner.display}")
    if note.attendees:
        parts.append("Attendees: " + ", ".join(a.display for a in note.attendees))
    if note.folder_names:
        parts.append("Folders: " + ", ".join(note.folder_names))
    if note.created_at:
        parts.append(f"Created: {note.created_at}")
    if note.updated_at and note.updated_at != note.created_at:
        parts.append(f"Updated: {note.updated_at}")
    parts.append("")

    summary = note.summary_markdown or note.summary_text
    if summary:
        parts.append("## Summary\n")
        parts.append(summary)
        parts.append("")

    if note.transcript:
        parts.append("## Transcript\n")
        # Merge consecutive turns from the same speaker. Granola chunks
        # transcript turns at silence/pause boundaries; without merging, a
        # continuous spoken sentence ends up split across multiple
        # `<speaker>:`-prefixed lines, which prevents the substring
        # verifier from matching quotes that span turn boundaries.
        current_speaker: str | None = None
        current_buffer: list[str] = []
        for turn in note.transcript:
            speaker = _speaker_label(turn.get("speaker"))
            text = (turn.get("text") or "").strip()
            if not text:
                continue
            if speaker == current_speaker:
                current_buffer.append(text)
            else:
                if current_buffer and current_speaker is not None:
                    parts.append(f"{current_speaker}: {' '.join(current_buffer)}")
                current_speaker = speaker
                current_buffer = [text]
        if current_buffer and current_speaker is not None:
            parts.append(f"{current_speaker}: {' '.join(current_buffer)}")

    return "\n".join(parts)


def _speaker_label(speaker: Any) -> str:
    """Flatten Granola's `{source, diarization_label?}` into a short label."""
    if isinstance(speaker, str):
        return speaker
    if isinstance(speaker, dict):
        if label := speaker.get("diarization_label"):
            return str(label)
        if source := speaker.get("source"):
            return str(source)
    return "speaker"
