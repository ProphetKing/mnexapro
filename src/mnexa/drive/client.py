# pyright: basic
"""Thin wrapper around the Google Drive v3 API.

Exposes only the operations Mnexa needs: walk a folder, get file metadata,
download (or export) file content. Lazy-imports the SDK so non-Drive
callers don't pay the import cost.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
GSLIDE = "application/vnd.google-apps.presentation"
GFOLDER = "application/vnd.google-apps.folder"

# Native Google Docs are exported on download; we pick the best representation.
EXPORT_TYPES: dict[str, tuple[str, str]] = {
    GDOC: ("text/markdown", ".md"),
    # Sheets / Slides intentionally omitted for v0.1; surface as warnings.
}


@dataclass
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    modified_time: str
    parents: list[str]
    md5: str | None
    size: int | None
    is_folder: bool


class DriveClient:
    def __init__(self, credentials: Any) -> None:
        from googleapiclient.discovery import build

        self._svc = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def get(self, file_id: str) -> DriveFile:
        meta = self._svc.files().get(
            fileId=file_id,
            fields="id,name,mimeType,modifiedTime,parents,md5Checksum,size",
            supportsAllDrives=True,
        ).execute()
        return _to_drivefile(meta)

    def walk(self, folder_id: str) -> Iterator[tuple[str, DriveFile]]:
        """Yield (drive_path, DriveFile) for every file under `folder_id`.

        `drive_path` is relative to the indexed folder root (the folder
        itself is not included in the path). Folders are not yielded.
        """
        root = self.get(folder_id)
        if not root.is_folder:
            raise ValueError(f"{folder_id} is not a folder")
        yield from self._walk(folder_id, prefix="")

    def _walk(self, folder_id: str, *, prefix: str) -> Iterator[tuple[str, DriveFile]]:
        page_token: str | None = None
        while True:
            resp = self._svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields=(
                    "nextPageToken,files(id,name,mimeType,modifiedTime,"
                    "parents,md5Checksum,size)"
                ),
                pageSize=200,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for raw in resp.get("files", []):
                df = _to_drivefile(raw)
                rel = f"{prefix}{df.name}"
                if df.is_folder:
                    yield from self._walk(df.file_id, prefix=f"{rel}/")
                else:
                    yield (rel, df)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    def download(self, df: DriveFile) -> tuple[bytes, str]:
        """Return (content_bytes, suggested_extension).

        For native Google Docs we export to markdown. For binary files we
        download as-is and the extension comes from the file's name.
        """
        from googleapiclient.http import MediaIoBaseDownload

        if df.mime_type in EXPORT_TYPES:
            export_mime, ext = EXPORT_TYPES[df.mime_type]
            req = self._svc.files().export_media(
                fileId=df.file_id, mimeType=export_mime
            )
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return buf.getvalue(), ext

        if df.mime_type.startswith("application/vnd.google-apps."):
            raise UnsupportedMimeError(
                f"{df.name}: {df.mime_type} is not supported in v0.1 (skip)"
            )

        req = self._svc.files().get_media(fileId=df.file_id, supportsAllDrives=True)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        # Use no extra extension; the file's own name carries it.
        return buf.getvalue(), ""


class UnsupportedMimeError(RuntimeError):
    pass


def _to_drivefile(raw: dict[str, Any]) -> DriveFile:
    return DriveFile(
        file_id=raw["id"],
        name=raw["name"],
        mime_type=raw["mimeType"],
        modified_time=raw.get("modifiedTime", ""),
        parents=raw.get("parents", []),
        md5=raw.get("md5Checksum"),
        size=int(raw["size"]) if raw.get("size") is not None else None,
        is_folder=raw["mimeType"] == GFOLDER,
    )
