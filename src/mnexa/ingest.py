"""Two-stage ingest pipeline (analyze → generate) over any source.

A source is a local file, a Drive file, a local folder, or a Drive folder.
The user types `mnexa ingest <anything>` and we dispatch.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
import os
from pathlib import Path
from datetime import datetime

MAX_LOG_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB

import typer

if TYPE_CHECKING:
    from mnexa.drive.client import DriveClient, DriveFile
    from mnexa.github.client import GitHubClient, GitHubFile
    from mnexa.granola.client import GranolaClient, GranolaNoteSummary

from mnexa import storage
from mnexa.llm import LLMClient, Usage, get_client
from mnexa.parser import parse_file_blocks
from mnexa.parsers import read_source
from mnexa.prompts import load as load_prompt
from mnexa.parser import IngestError

MAX_SOURCE_BYTES = 200_000_000
MAX_RELATED_PAGES = 10
FOLDER_CONFIRM_THRESHOLD = 5


# --- target classification & data shapes -----------------------------------


TargetKind = Literal[
    "local-file", "local-folder",
    "drive-file", "drive-folder",
    "granola-note", "granola-list",
    "github-file", "github-repo",
]


@dataclass(frozen=True)
class IngestTarget:
    kind: TargetKind
    local_path: Path | None = None
    external_id: str | None = None
    since: str | None = None  # only used by granola-list
    # github-specific
    github_owner: str | None = None
    github_repo: str | None = None
    github_branch: str | None = None  # None = default branch
    github_path: str | None = None    # only for github-file kind


@dataclass(frozen=True)
class DriveMeta:
    file_id: str
    modified_time: str
    web_view_link: str
    drive_path: str
    mime_type: str


@dataclass(frozen=True)
class GranolaMeta:
    note_id: str
    created_at: str
    updated_at: str
    web_url: str
    attendees: list[str]
    folder_names: list[str]


@dataclass(frozen=True)
class GitHubMeta:
    owner: str
    repo: str
    branch: str
    path: str
    blob_sha: str
    html_url: str


@dataclass(frozen=True)
class IngestSource:
    filename: str       # display name, e.g., "foo.pdf" or "README.md"
    text: str           # parsed plain text fed to the LLM
    hash: str           # sha256 of raw bytes (for change detection)
    source_path: str    # frontmatter source_path
    drive_meta: DriveMeta | None = None
    granola_meta: GranolaMeta | None = None
    github_meta: GitHubMeta | None = None


_DRIVE_FOLDER_RE = re.compile(r"/folders/([a-zA-Z0-9_-]{8,})")
_DRIVE_FILE_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]{8,})")
_DRIVE_QUERY_ID_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]{8,})")
_DRIVE_SCHEME_RE = re.compile(r"^drive://([a-zA-Z0-9_-]{8,})")

_GRANOLA_NOTE_ID_RE = re.compile(r"(not_[a-zA-Z0-9]{14})")
_GRANOLA_NOTE_SCHEME_RE = re.compile(r"^granola://note/(not_[a-zA-Z0-9]{14})$")
_GRANOLA_SINCE_RE = re.compile(r"^granola://since/(.+)$")
_GRANOLA_SHARE_URL_RE = re.compile(
    r"notes\.granola\.ai/d/([a-f0-9-]{36})"
)

# GitHub (browser URLs only support single-segment branches; use the
# `github://` scheme for branches with slashes if needed in future).
_GITHUB_FILE_URL_RE = re.compile(
    r"github\.com/([\w.-]+)/([\w.-]+)/blob/([\w.-]+)/(.+)$"
)
_GITHUB_TREE_URL_RE = re.compile(
    r"github\.com/([\w.-]+)/([\w.-]+)/tree/([\w.-]+)/?$"
)
_GITHUB_REPO_URL_RE = re.compile(
    r"github\.com/([\w.-]+)/([\w.-]+?)/?$"
)
_GITHUB_SCHEME_FILE_RE = re.compile(
    r"^github://([\w.-]+)/([\w.-]+)/(.+)$"
)
_GITHUB_SCHEME_REPO_RE = re.compile(
    r"^github://([\w.-]+)/([\w.-]+?)/?$"
)


def classify_target(arg: str) -> IngestTarget:
    arg = arg.strip()

    # Drive
    if "drive.google.com" in arg or arg.startswith("drive://"):
        if "/folders/" in arg:
            return IngestTarget(
                "drive-folder", external_id=_extract_drive_folder_id(arg)
            )
        return IngestTarget("drive-file", external_id=_extract_drive_file_id(arg))

    # GitHub
    if "github.com" in arg or arg.startswith("github://"):
        if (m := _GITHUB_FILE_URL_RE.search(arg)):
            return IngestTarget(
                "github-file",
                github_owner=m.group(1), github_repo=m.group(2),
                github_branch=m.group(3), github_path=m.group(4),
            )
        if (m := _GITHUB_TREE_URL_RE.search(arg)):
            return IngestTarget(
                "github-repo",
                github_owner=m.group(1), github_repo=m.group(2),
                github_branch=m.group(3),
            )
        if (m := _GITHUB_SCHEME_FILE_RE.fullmatch(arg)):
            return IngestTarget(
                "github-file",
                github_owner=m.group(1), github_repo=m.group(2),
                github_path=m.group(3),
            )
        if (m := _GITHUB_SCHEME_REPO_RE.fullmatch(arg)):
            return IngestTarget(
                "github-repo",
                github_owner=m.group(1), github_repo=m.group(2),
            )
        if (m := _GITHUB_REPO_URL_RE.search(arg)):
            return IngestTarget(
                "github-repo",
                github_owner=m.group(1), github_repo=m.group(2),
            )
        raise typer.BadParameter(f"unrecognized GitHub argument: {arg!r}")

    # Granola
    if "granola.ai" in arg or arg.startswith("granola") or _GRANOLA_NOTE_ID_RE.fullmatch(arg):
        if _GRANOLA_SHARE_URL_RE.search(arg):
            raise typer.BadParameter(
                "Granola share URLs (notes.granola.ai/d/<uuid>) use a "
                "different identifier than the API. Pass the note ID "
                "(format: not_<14 chars>) instead, e.g. "
                "`mnexa ingest granola://note/not_1d3tmYTlCICgjy`."
            )
        m = _GRANOLA_NOTE_SCHEME_RE.search(arg)
        if m:
            return IngestTarget("granola-note", external_id=m.group(1))
        if (m := _GRANOLA_NOTE_ID_RE.fullmatch(arg)):
            return IngestTarget("granola-note", external_id=m.group(1))
        m = _GRANOLA_SINCE_RE.search(arg)
        if m:
            return IngestTarget("granola-list", since=m.group(1))
        if arg in {"granola", "granola://", "granola://recent"}:
            return IngestTarget("granola-list")
        raise typer.BadParameter(f"unrecognized Granola argument: {arg!r}")

    # Local
    p = Path(arg).expanduser()
    if p.is_dir():
        return IngestTarget("local-folder", local_path=p.resolve())
    if p.is_file():
        return IngestTarget("local-file", local_path=p.resolve())
    raise typer.BadParameter(
        f"can't interpret {arg!r} as a file, folder, or supported URL"
    )


def _extract_drive_folder_id(url: str) -> str:
    m = _DRIVE_FOLDER_RE.search(url)
    if not m:
        raise typer.BadParameter(f"could not parse Drive folder ID from {url!r}")
    return m.group(1)


def _extract_drive_file_id(url: str) -> str:
    for pattern in (_DRIVE_FILE_RE, _DRIVE_QUERY_ID_RE, _DRIVE_SCHEME_RE):
        m = pattern.search(url)
        if m:
            return m.group(1)
    raise typer.BadParameter(f"could not parse Drive file ID from {url!r}")


# --- entry point ------------------------------------------------------------


def run(target: str | Path, *, client: LLMClient | None = None,
        yes: bool = False, limit: int | None = None,
        since: str | None = None) -> None:
    asyncio.run(_run_async(
        str(target), client=client, yes=yes, limit=limit, since=since,
    ))


async def _run_async(target: str, *, client: LLMClient | None,
                     yes: bool, limit: int | None, since: str | None) -> None:
    vault = storage.find_vault(Path.cwd())
    if vault is None:
        typer.echo(
            "error: not inside an Mnexa vault (run `mnexa init` first)", err=True
        )
        raise typer.Exit(1)

    tgt = classify_target(target)

    if client is None:
        client = get_client()

    if tgt.kind == "local-file":
        assert tgt.local_path is not None
        source = _load_local_source(tgt.local_path, vault)
        await _ingest_one(source, vault=vault, client=client)
        return

    if tgt.kind == "local-folder":
        assert tgt.local_path is not None
        await _ingest_local_folder(tgt.local_path, vault=vault, client=client,
                                   yes=yes, limit=limit)
        return

    if tgt.kind == "drive-file":
        assert tgt.external_id is not None
        from mnexa.drive.auth import get_credentials
        from mnexa.drive.client import DriveClient
        creds = get_credentials()
        drive_client = DriveClient(creds)
        source = _load_drive_source(tgt.external_id, drive_client)
        await _ingest_one(source, vault=vault, client=client)
        return

    if tgt.kind == "drive-folder":
        assert tgt.external_id is not None
        from mnexa.drive.auth import get_credentials
        from mnexa.drive.client import DriveClient
        creds = get_credentials()
        drive_client = DriveClient(creds)
        await _ingest_drive_folder(
            tgt.external_id, vault=vault, client=client,
            drive_client=drive_client, yes=yes, limit=limit,
        )
        return

    if tgt.kind == "granola-note":
        assert tgt.external_id is not None
        from mnexa.granola.auth import get_api_key
        from mnexa.granola.client import GranolaClient
        gc = GranolaClient(get_api_key())
        try:
            source = _load_granola_source(tgt.external_id, gc)
            await _ingest_one(source, vault=vault, client=client)
        finally:
            gc.close()
        return

    if tgt.kind == "granola-list":
        from mnexa.granola.auth import get_api_key
        from mnexa.granola.client import GranolaClient
        gc = GranolaClient(get_api_key())
        try:
            await _ingest_granola_list(
                vault=vault, client=client, granola_client=gc,
                yes=yes, limit=limit, since=since or tgt.since,
            )
        finally:
            gc.close()
        return

    if tgt.kind == "github-file":
        assert tgt.github_owner is not None
        assert tgt.github_repo is not None
        assert tgt.github_path is not None
        from mnexa.github.auth import get_token
        from mnexa.github.client import GitHubClient
        ghc = GitHubClient(get_token())
        try:
            branch = tgt.github_branch or ghc.default_branch(
                tgt.github_owner, tgt.github_repo,
            )
            content, file = ghc.get_file(
                tgt.github_owner, tgt.github_repo, tgt.github_path, branch,
            )
            source = _make_github_source(file, content)
            await _ingest_one(source, vault=vault, client=client)
        finally:
            ghc.close()
        return

    if tgt.kind == "github-repo":
        assert tgt.github_owner is not None
        assert tgt.github_repo is not None
        from mnexa.github.auth import get_token
        from mnexa.github.client import GitHubClient
        ghc = GitHubClient(get_token())
        try:
            await _ingest_github_repo(
                owner=tgt.github_owner, repo=tgt.github_repo,
                branch=tgt.github_branch, vault=vault, client=client,
                github_client=ghc, yes=yes, limit=limit,
            )
        finally:
            ghc.close()
        return


# --- folder ingest ----------------------------------------------------------


@dataclass(frozen=True)
class _ExistingExternalPage:
    path: Path
    modified: str | None
    hash: str | None


def _existing_external_pages(
    vault: Path, *, id_field: str, mtime_field: str,
) -> dict[str, _ExistingExternalPage]:
    """Map external-id → existing wiki page metadata, by reading frontmatter.

    Generic over the id field (`drive_file_id`, `granola_note_id`, etc.) and
    the corresponding modified-time field.
    """
    out: dict[str, _ExistingExternalPage] = {}
    sources = vault / "wiki" / "sources"
    if not sources.is_dir():
        return out
    for p in sources.glob("*.md"):
        fm = storage.read_frontmatter(p)
        ident = fm.get(id_field)
        if isinstance(ident, str) and ident:
            mod = _normalize_timestamp(fm.get(mtime_field))
            h = fm.get("hash")
            out[ident] = _ExistingExternalPage(
                path=p,
                modified=mod,
                hash=h if isinstance(h, str) else None,
            )
    return out


def _normalize_timestamp(value: object) -> str | None:
    """Coerce a frontmatter timestamp value to canonical RFC 3339 string.

    YAML auto-parses unquoted ISO-8601 timestamps to `datetime`, which breaks
    string equality checks against Drive's `modifiedTime` (which is always a
    string from the API). Normalise both to the same string form.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        ms = value.microsecond // 1000
        return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"
    return None


async def _ingest_drive_folder(folder_id: str, *, vault: Path, client: LLMClient,
                               drive_client: DriveClient, yes: bool,
                               limit: int | None) -> None:
    typer.echo(f"[ingest] scanning Drive folder {folder_id}…", err=True)
    items = list(drive_client.walk(folder_id))
    if not items:
        typer.echo("[ingest] folder is empty", err=True)
        return

    existing = _existing_external_pages(
        vault, id_field="drive_file_id", mtime_field="drive_modified",
    )
    pending: list[tuple[str, DriveFile]] = []
    skipped = 0
    for drive_path, df in items:
        prev = existing.get(df.file_id)
        if prev is not None and prev.modified == df.modified_time:
            skipped += 1
            continue
        pending.append((drive_path, df))

    if limit is not None:
        pending = pending[:limit]

    typer.echo(
        f"[ingest] {len(items)} files in folder · {len(pending)} new/changed · "
        f"{skipped} unchanged",
        err=True,
    )
    if not pending:
        return

    if (
        not yes and len(pending) >= FOLDER_CONFIRM_THRESHOLD
        and not typer.confirm(f"proceed with {len(pending)} ingests?")
    ):
        typer.echo("[ingest] aborted", err=True)
        return

    succeeded = 0
    failed = 0
    for i, (drive_path, df) in enumerate(pending, 1):
        typer.echo(f"[{i}/{len(pending)}] {drive_path}", err=True)
        try:
            source = _load_drive_source(df.file_id, drive_client)
            await _ingest_one(source, vault=vault, client=client)
            succeeded += 1
        except (RuntimeError, OSError, ValueError) as e:
            typer.echo(f"  failed: {e}", err=True)
            failed += 1
            continue

    typer.echo(
        f"[ingest] folder done · {succeeded} ingested · {failed} failed", err=True
    )


async def _ingest_granola_list(*, vault: Path, client: LLMClient,
                               granola_client: GranolaClient, yes: bool,
                               limit: int | None, since: str | None) -> None:
    typer.echo("[ingest] listing Granola notes…", err=True)
    # Use updated_after so we capture both new notes and edits to existing ones.
    summaries = list(granola_client.list_notes(updated_after=since))
    if not summaries:
        typer.echo("[ingest] no notes returned", err=True)
        return

    existing = _existing_external_pages(
        vault, id_field="granola_note_id", mtime_field="granola_updated",
    )
    pending: list[GranolaNoteSummary] = []
    skipped = 0
    for s in summaries:
        prev = existing.get(s.note_id)
        if prev is not None and prev.modified == s.updated_at:
            skipped += 1
            continue
        pending.append(s)

    if limit is not None:
        pending = pending[:limit]

    typer.echo(
        f"[ingest] {len(summaries)} notes total · {len(pending)} new/changed · "
        f"{skipped} unchanged",
        err=True,
    )
    if not pending:
        return

    if (
        not yes and len(pending) >= FOLDER_CONFIRM_THRESHOLD
        and not typer.confirm(f"proceed with {len(pending)} ingests?")
    ):
        typer.echo("[ingest] aborted", err=True)
        return

    succeeded = 0
    failed = 0
    for i, s in enumerate(pending, 1):
        typer.echo(f"[{i}/{len(pending)}] {s.title}", err=True)
        try:
            source = _load_granola_source(s.note_id, granola_client)
            await _ingest_one(source, vault=vault, client=client)
            succeeded += 1
        except (RuntimeError, OSError, ValueError) as e:
            typer.echo(f"  failed: {e}", err=True)
            failed += 1
            continue

    typer.echo(
        f"[ingest] granola list done · {succeeded} ingested · {failed} failed",
        err=True,
    )


async def _ingest_github_repo(*, owner: str, repo: str, branch: str | None,
                              vault: Path, client: LLMClient,
                              github_client: GitHubClient, yes: bool,
                              limit: int | None) -> None:
    if branch is None:
        branch = github_client.default_branch(owner, repo)
    typer.echo(f"[ingest] scanning github://{owner}/{repo}@{branch}…", err=True)

    files = github_client.list_top_level_md(owner, repo, branch)
    if not files:
        typer.echo("[ingest] no top-level .md files found", err=True)
        return

    existing = _existing_external_pages(
        vault, id_field="github_url", mtime_field="github_blob_sha",
    )
    pending: list[GitHubFile] = []
    skipped = 0
    for f in files:
        prev = existing.get(f.html_url)
        if prev is not None and prev.modified == f.blob_sha:
            skipped += 1
            continue
        pending.append(f)

    if limit is not None:
        pending = pending[:limit]

    typer.echo(
        f"[ingest] {len(files)} top-level .md files · {len(pending)} new/changed · "
        f"{skipped} unchanged",
        err=True,
    )
    if not pending:
        return

    if (
        not yes and len(pending) >= FOLDER_CONFIRM_THRESHOLD
        and not typer.confirm(f"proceed with {len(pending)} ingests?")
    ):
        typer.echo("[ingest] aborted", err=True)
        return

    succeeded = 0
    failed = 0
    for i, f in enumerate(pending, 1):
        typer.echo(f"[{i}/{len(pending)}] {f.path}", err=True)
        try:
            content, _ = github_client.get_file(f.owner, f.repo, f.path, f.branch)
            source = _make_github_source(f, content)
            await _ingest_one(source, vault=vault, client=client)
            succeeded += 1
        except (RuntimeError, OSError, ValueError) as e:
            typer.echo(f"  failed: {e}", err=True)
            failed += 1
            continue

    typer.echo(
        f"[ingest] github done · {succeeded} ingested · {failed} failed",
        err=True,
    )

async def _ingest_local_folder(folder: Path, *, vault: Path, client: LLMClient,
                               yes: bool, limit: int | None) -> None:
    typer.echo(f"[ingest] scanning local folder {folder}…", err=True)
    
    # 【核心修改】扩展支持的后缀名，包含 markitdown 支持的所有格式
    SUPPORTED_SUFFIXES = {
        ".md", ".markdown", ".txt", "", 
        ".pdf", ".docx", ".doc", 
        ".pptx", ".ppt", 
        ".xlsx", ".xls", 
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff",
        ".wav", ".mp3", ".m4a"
    }

    files: list[Path] = sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    
    if limit is not None:
        files = files[:limit]
        
    if not files:
        typer.echo("[ingest] no supported files found", err=True)
        return

    typer.echo(f"[ingest] {len(files)} files to consider", err=True)
    
    if (
        not yes and len(files) >= FOLDER_CONFIRM_THRESHOLD
        and not typer.confirm(f"proceed with {len(files)} ingests?")
    ):
        typer.echo("[ingest] aborted", err=True)
        return

    succeeded = 0
    failed = 0
    for i, file in enumerate(files, 1):
        # 显示相对于当前扫描文件夹的路径
        typer.echo(f"[{i}/{len(files)}] Processing: {file.relative_to(folder)}", err=True)
        try:
            source = _load_local_source(file, vault)
            await _ingest_one(source, vault=vault, client=client)
            succeeded += 1
            typer.echo(f"  [OK] Successfully ingested {file.name}", err=True)
            await asyncio.sleep(30)   # 新增延迟
        except Exception as e:
            # 【核心修改】捕获所有异常，记录错误但不中断循环
            failed += 1
            error_msg = f"  [FAILED] Error processing {file.name}: {e}"
            typer.echo(error_msg, err=True)
            
            # 可选：将严重错误也记入日志文件，方便后续排查
            await asyncio.sleep(30)   # 新增延迟
            log_path = vault / "wiki" / "log.md"
            if log_path.exists():
                try:
                    import os
                    fd = os.open(str(log_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                    os.write(fd, f"\n[INGEST ERROR] {file.name}: {e}\n".encode('utf-8'))
                    os.close(fd)
                except:
                    pass
            
            continue # 关键：跳过当前失败的文件，继续处理下一个

    typer.echo(
        f"\n[ingest] Summary: {succeeded} succeeded, {failed} failed out of {len(files)} total.", 
        err=True
    )
# async def _ingest_local_folder(folder: Path, *, vault: Path, client: LLMClient,
#                                yes: bool, limit: int | None) -> None:
#     typer.echo(f"[ingest] scanning local folder {folder}…", err=True)
#     files: list[Path] = sorted(
#         p for p in folder.rglob("*")
#         if p.is_file() and p.suffix.lower() in {".md", ".markdown", ".txt", ".pdf", ".docx"}
#     )
#     if limit is not None:
#         files = files[:limit]
#     if not files:
#         typer.echo("[ingest] no supported files found", err=True)
#         return

#     typer.echo(f"[ingest] {len(files)} files to consider", err=True)
#     if (
#         not yes and len(files) >= FOLDER_CONFIRM_THRESHOLD
#         and not typer.confirm(f"proceed with {len(files)} ingests?")
#     ):
#         typer.echo("[ingest] aborted", err=True)
#         return

#     succeeded = 0
#     failed = 0
#     for i, file in enumerate(files, 1):
#         typer.echo(f"[{i}/{len(files)}] {file.relative_to(folder)}", err=True)
#         try:
#             source = _load_local_source(file, vault)
#             await _ingest_one(source, vault=vault, client=client)
#             succeeded += 1
#         except (RuntimeError, OSError, ValueError) as e:
#             typer.echo(f"  failed: {e}", err=True)
#             failed += 1
#             continue

#     typer.echo(
#         f"[ingest] folder done · {succeeded} ingested · {failed} failed", err=True
#     )


# --- source loaders ---------------------------------------------------------

# 在 d:\Develop_AI\mnexa\mnexa\src\mnexa\ingest.py 中找到 _load_local_source 函数并完整替换

def _load_local_source(file: Path, vault: Path) -> IngestSource:
    file = file.expanduser().resolve()
    if not file.is_file():
        raise ValueError(f"not a file: {file}")
    
    raw_bytes = file.read_bytes()
    if len(raw_bytes) > MAX_SOURCE_BYTES:
        raise ValueError(
            f"source is {len(raw_bytes)} bytes; v0 limit is {MAX_SOURCE_BYTES}"
        )
    
    text = read_source(file)
    
    # 【核心修改】计算相对于 vault/raw 的路径，支持层级结构
    raw_root = vault / "raw"
    try:
        # 如果文件在 raw 目录下，获取相对路径；否则回退到仅文件名
        rel_path = file.relative_to(raw_root)
        source_path = f"raw/{rel_path.as_posix()}"
    except ValueError:
        # 如果文件不在 raw 目录下（比如直接 ingest 外部文件），则复制到 raw 根目录
        rel_path = Path(file.name)
        source_path = f"raw/{file.name}"

    raw_dest = vault / "raw" / rel_path
    # 确保目标目录存在
    raw_dest.parent.mkdir(parents=True, exist_ok=True)
    
    # 只有当目标不存在时才复制，避免覆盖已存在的同名不同内容文件（除非是重新摄入）
    if not raw_dest.exists():
        shutil.copy2(file, raw_dest)
        
    return IngestSource(
        filename=file.name,
        text=text,
        hash=hashlib.sha256(raw_bytes).hexdigest(),
        source_path=source_path,
        drive_meta=None,
    )
# def _load_local_source(file: Path, vault: Path) -> IngestSource:
#     file = file.expanduser().resolve()
#     if not file.is_file():
#         raise ValueError(f"not a file: {file}")
#     raw_bytes = file.read_bytes()
#     if len(raw_bytes) > MAX_SOURCE_BYTES:
#         raise ValueError(
#             f"source is {len(raw_bytes)} bytes; v0 limit is {MAX_SOURCE_BYTES}"
#         )
#     text = read_source(file)
#     raw_dest = vault / "raw" / file.name
#     if file.parent.resolve() != (vault / "raw").resolve() and not raw_dest.exists():
#         shutil.copy2(file, raw_dest)
#     return IngestSource(
#         filename=file.name,
#         text=text,
#         hash=hashlib.sha256(raw_bytes).hexdigest(),
#         source_path=f"raw/{file.name}",
#         drive_meta=None,
#     )


def _make_github_source(file: GitHubFile, content: bytes) -> IngestSource:
    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError(
            f"{file.path} is {len(content)} bytes; v0 limit is {MAX_SOURCE_BYTES}"
        )
    text = content.decode("utf-8", errors="replace")
    return IngestSource(
        filename=file.path,
        text=text,
        hash=hashlib.sha256(content).hexdigest(),
        source_path=f"github://{file.owner}/{file.repo}/{file.path}",
        github_meta=GitHubMeta(
            owner=file.owner,
            repo=file.repo,
            branch=file.branch,
            path=file.path,
            blob_sha=file.blob_sha,
            html_url=file.html_url,
        ),
    )


def _load_granola_source(note_id: str, granola_client: GranolaClient) -> IngestSource:
    from mnexa.granola.client import render_note_text
    note = granola_client.get_note(note_id)
    text = render_note_text(note)
    if len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError(
            f"note {note_id} text is {len(text.encode('utf-8'))} bytes; "
            f"v0 limit is {MAX_SOURCE_BYTES}"
        )
    attendee_displays = [a.display for a in note.attendees]
    return IngestSource(
        filename=note.title,
        text=text,
        hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_path=f"granola://{note.note_id}",
        drive_meta=None,
        granola_meta=GranolaMeta(
            note_id=note.note_id,
            created_at=note.created_at,
            updated_at=note.updated_at,
            web_url=note.web_url,
            attendees=attendee_displays,
            folder_names=note.folder_names,
        ),
    )


def _load_drive_source(file_id: str, drive_client: DriveClient) -> IngestSource:
    df = drive_client.get(file_id)
    if df.is_folder:
        raise ValueError(f"{file_id} is a folder, not a file")
    content_bytes, ext = drive_client.download(df)
    if len(content_bytes) > MAX_SOURCE_BYTES:
        raise ValueError(
            f"source is {len(content_bytes)} bytes; v0 limit is {MAX_SOURCE_BYTES}"
        )
    filename = df.name + ext if ext and not df.name.endswith(ext) else df.name
    text = _bytes_to_text(content_bytes, df.mime_type, filename)
    return IngestSource(
        filename=filename,
        text=text,
        hash=hashlib.sha256(content_bytes).hexdigest(),
        source_path=f"drive://{df.file_id}",
        drive_meta=DriveMeta(
            file_id=df.file_id,
            modified_time=df.modified_time,
            web_view_link=f"https://drive.google.com/file/d/{df.file_id}/view",
            drive_path=df.name,
            mime_type=df.mime_type,
        ),
    )


def _bytes_to_text(content: bytes, mime_type: str, filename: str) -> str:
    if mime_type in {"text/plain", "text/markdown"}:
        return content.decode("utf-8", errors="replace")
    suffix = Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(content)
        tmp_path = Path(tf.name)
    try:
        return read_source(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


# --- per-source pipeline ----------------------------------------------------
# 在 d:\Develop_AI\mnexa\mnexa\src\mnexa\ingest.py 中找到 _ingest_one 函数并完整替换

async def _ingest_one(source: IngestSource, *, vault: Path, client: LLMClient) -> None:
    """
    【绝对安全日志版】
    1. 彻底移除 Git 提交与回滚逻辑。
    2. 直接将解析出的 FILE 块写入 wiki/ 对应的路径。
    3. 【核心】使用 OS 级追加模式写入日志，确保永不覆盖。
    4. 【新增】当日志文件超过 5MB 时，自动归档并新建日志文件。
    """
    log_path = vault / "wiki" / "log.md"
    
    # 确保 log.md 存在
    if not log_path.exists():
        log_path.write_text("# Log\n\n", encoding="utf-8")

    # 日志轮转配置
    MAX_LOG_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
    def rotate_log_if_needed(log_path: Path):
        """如果 log.md 大小超过限制，则重命名为 log_YYYYMMDD_HHMMSS.md，并创建新的空日志文件"""
        try:
            if log_path.exists() and log_path.stat().st_size > MAX_LOG_SIZE_BYTES:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_name = f"log_{timestamp}.md"
                backup_path = log_path.parent / backup_name
                # 如果备份文件已存在（罕见），加上进程ID
                if backup_path.exists():
                    backup_path = log_path.parent / f"log_{timestamp}_{os.getpid()}.md"
                log_path.rename(backup_path)
                # 创建全新的空白日志文件，包含标题
                log_path.write_text("# Log\n\n", encoding="utf-8")
                typer.echo(f"[LOG ROTATION] Archived to {backup_path.name}", err=True)
        except Exception as e:
            typer.echo(f"[LOG ROTATION ERROR] {e}", err=True)

    def safe_append_log(log_path: Path, msg: str):
        """
        使用 os.open 追加模式写入日志，确保绝不会清空已有内容。
        如果文件不存在，会自动创建。
        """
        try:
            # 确保目录存在
            log_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(log_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT)
            try:
                os.write(fd, msg.encode('utf-8'))
            finally:
                os.close(fd)
        except Exception as e:
            typer.echo(f"[LOG WRITE FAILED] {e}", err=True)
    # def rotate_log_if_needed():
    #     """如果 log.md 超过大小限制，则重命名并创建新文件"""
    #     try:
    #         # 再次检查大小，因为可能在检查后瞬间被其他进程写入
    #         if log_path.exists() and log_path.stat().st_size > MAX_LOG_SIZE_BYTES:
    #             timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    #             backup_name = f"log_{timestamp}.md"
    #             backup_path = log_path.parent / backup_name
                
    #             # 如果备份文件已存在（极高并发下可能），加个随机后缀
    #             if backup_path.exists():
    #                 backup_path = log_path.parent / f"log_{timestamp}_{os.getpid()}.md"

    #             # 执行重命名 (原子操作)
    #             log_path.rename(backup_path)
                
    #             # 创建全新的空日志文件
    #             log_path.write_text("# Log\n\n", encoding="utf-8")
                
    #             typer.echo(f"[LOG ROTATION] Archived to {backup_path.name}", err=True)
    #     except Exception as e:
    #         # 轮转失败不应阻断 ingest 流程，仅记录错误
    #         typer.echo(f"[LOG ROTATION ERROR] {e}", err=True)

    # def safe_append_log(msg: str):
    #     """
    #     使用底层 os.open 确保以追加模式写入，避免任何覆盖风险。
    #     """
    #     try:
    #         # 1. 检查是否需要轮转
    #         rotate_log_if_needed()

    #         # 2. 使用 os.open 获取文件描述符，指定 O_WRONLY | O_APPEND | O_CREAT
    #         # 这种方式比 open('a') 更底层，能更好处理 Windows 下的文件锁问题
    #         fd = os.open(str(log_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    #         try:
    #             os.write(fd, msg.encode('utf-8'))
    #         finally:
    #             os.close(fd)
    #     except Exception as e:
    #         # 极端情况：如果连底层追加都失败，打印到终端警告
    #         typer.echo(f"[LOG WRITE FAILED] {e}", err=True)

    def sync_output(msg: str, is_chunk: bool = False):
        # 写入日志（绝对追加）
        rotate_log_if_needed(log_path)  # 每次写前检查大小
        safe_append_log(log_path, msg)
        # 写入终端
        if not is_chunk:
            typer.echo(msg, nl=False, err=True)
        else:
            try:
                sys.stderr.write(msg)
                sys.stderr.flush()
            except OSError:
                pass

    try:
        # --- Stage 1: Analysis ---
        schema_raw = (vault / "CLAUDE.md").read_text(encoding="utf-8")
        MAX_SCHEMA_CHARS = 4000  # 限制 schema 长度，避免系统消息过大
        if len(schema_raw) > MAX_SCHEMA_CHARS:
            schema = schema_raw[:MAX_SCHEMA_CHARS] + "\n\n[schema 过长，已截断]"
        else:
            schema = schema_raw
        index = (vault / "wiki" / "index.md").read_text(encoding="utf-8")
        related = _find_related_pages(vault, source.text, MAX_RELATED_PAGES)
        today = date.today().isoformat()

        sync_output(f"[{today}] START INGEST: {source.filename}\n")
        
        stage1_system = _build_system("stage1.md", schema)
        stage1_user = _build_stage1_user(
            index=index, related=related, vault=vault, source=source,
        )
        
        sync_output("  [stage 1] analyzing...\n")
        completion = await client.complete(
            system=stage1_system, user=stage1_user, cache_system=True
        )
        analysis = completion.text
        
        # 【要求】Stage 1 Think 内容立即落盘
        sync_output(f"\n--- STAGE 1 THINK CONTENT ---\n{analysis}\n--- END THINK ---\n")
        sync_output("  [stage 1] done.\n")

        # --- Stage 2: Generation (Stream) ---
        sync_output("  [stage 2] generating...\n")

        existing = _gather_existing_pages(vault, analysis)
        stage2_system = _build_system("stage2.md", schema)
        stage2_user = _build_stage2_user(
            analysis=analysis, vault=vault, source=source,
            existing=existing, today=today,
        )

        import tempfile
        output_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.md', encoding='utf-8') as tmp_file:
                output_path = tmp_file.name
                
                async for chunk in client.stream(
                    system=stage2_system, user=stage2_user, cache_system=True
                ):
                    # 【核心要求】终端输出一句，wiki/log.md 写一句
                    sync_output(chunk, is_chunk=True)
                    
                    # 同时写入临时文件用于解析
                    tmp_file.write(chunk)
                    tmp_file.flush()

            output = Path(output_path).read_text(encoding='utf-8')
            sync_output("\n[DEBUG] Stage 2 stream completed.\n")
            
        except Exception as e:
            error_msg = f"\n[ERROR] Stage 2 Stream Failed: {e}\n"
            sync_output(error_msg)
            raise RuntimeError(error_msg) from e
        finally:
            if output_path and Path(output_path).exists():
                Path(output_path).unlink()

        if not output.strip():
            raise RuntimeError("Stage 2 returned empty content.")

        # --- Parse & Write Immediately (NO GIT) ---
        try:
            blocks = parse_file_blocks(output, vault)
            typer.echo(f"  [DEBUG] Parsed {len(blocks)} FILE blocks.", err=True)
        except IngestError as e:
            typer.echo(f"  [warn] Parse error (ignored): {e}", err=True)
            blocks = []  # 跳过错误块，继续处理

        if not blocks:
            sync_output("[INFO] No FILE blocks emitted.\n")
            return

        # 【关键修改】直接写入文件，不调用 storage.write_pages (避免其内部的 fsync 报错)
        for block in blocks:
            try:
                # 确保目录存在
                block.abs_path.parent.mkdir(parents=True, exist_ok=True)
                # 直接写入内容
                block.abs_path.write_text(block.raw_content, encoding='utf-8')
                sync_output(f"  [SAVED] {block.rel_path}\n")
            except Exception as e:
                sync_output(f"  [ERROR] Failed to save {block.rel_path}: {e}\n")
                # 即使某个文件保存失败，也继续尝试保存其他文件

        sync_output(f"\n[SUCCESS] All files saved to disk. Git operations skipped.\n")
        sync_output(f"[INFO] Please check your wiki/ folder manually.\n")

    except Exception as e:
        # 【要求】有一句报错立即退出，但此时文件应该已经写在磁盘上了
        final_error = f"\n[FATAL] Process aborted: {e}\n"
        try:
            sync_output(final_error)
        except:
            pass
        raise
# async def _ingest_one(source: IngestSource, *, vault: Path, client: LLMClient) -> None:
#     schema = (vault / "CLAUDE.md").read_text(encoding="utf-8")
#     index = (vault / "wiki" / "index.md").read_text(encoding="utf-8")
#     related = _find_related_pages(vault, source.text, MAX_RELATED_PAGES)
#     today = date.today().isoformat()

#     typer.echo(f"  [stage 1] analyzing {source.filename}…", err=True)
#     stage1_system = _build_system("stage1.md", schema)
#     stage1_user = _build_stage1_user(
#         index=index, related=related, vault=vault, source=source,
#     )
#     completion = await client.complete(
#         system=stage1_system, user=stage1_user, cache_system=True
#     )
#     analysis = completion.text
#     typer.echo(f"  [stage 1] done · {_fmt_usage(completion.usage)}", err=True)

#     typer.echo("  [stage 2] generating wiki updates…", err=True)
#     existing = _gather_existing_pages(vault, analysis)
#     stage2_system = _build_system("stage2.md", schema)
#     stage2_user = _build_stage2_user(
#         analysis=analysis, vault=vault, source=source,
#         existing=existing, today=today,
#     )

#     # accumulated: list[str] = []
#     # async for chunk in client.stream(
#     #     system=stage2_system, user=stage2_user, cache_system=True
#     # ):
#     #     sys.stderr.write(chunk)
#     #     sys.stderr.flush()
#     #     accumulated.append(chunk)
#     # sys.stderr.write("\n")
#     accumulated: list[str] = []
#     try:
#         async for chunk in client.stream(
#             system=stage2_system, user=stage2_user, cache_system=True
#         ):
#             # 使用 typer.echo 写入 stderr，它在 Windows 上更稳定
#             typer.echo(chunk, nl=False, err=True)
#             accumulated.append(chunk)
#         typer.echo("", err=True) # 换行
#     except OSError as e:
#         # 捕获可能的 Bad file descriptor 或其他 IO 错误
#         typer.echo(f"\n[warning] stream output error: {e}", err=True)
#         # 即使输出失败，我们仍然可能有 accumulated 的内容用于解析
#         if not accumulated:
#             raise
#     output = "".join(accumulated)
#     if client.last_usage is not None:
#         typer.echo(f"  [stage 2] done · {_fmt_usage(client.last_usage)}", err=True)

#     blocks = parse_file_blocks(output, vault)
#     if not blocks:
#         typer.echo("  no changes (Stage 2 emitted no FILE blocks)", err=True)
#         return

#     # Strict substring grounding for stable-text sources (local files, PDFs
#     # via Drive) where the LLM has no reason to paraphrase and verbatim
#     # quoting survives. Relaxed for sources where the LLM must paraphrase
#     # to bridge structural breaks: transcript chunking (Granola), markdown
#     # formatting + i18n (GitHub). For relaxed sources, click-through to
#     # the canonical URL in frontmatter is the verification path.
#     require_substring = source.granola_meta is None and source.github_meta is None
#     verify_grounding(blocks, source.text, require_substring=require_substring)

#     pages = {b.abs_path: b.raw_content for b in blocks}
#     try:
#         storage.write_pages(vault, pages)
#     except Exception:
#         storage.git_rollback(vault)
#         raise

#     if not storage.git_commit(vault, f"ingest: {source.filename}"):
#         typer.echo("  warning: write succeeded but no git changes detected", err=True)
#         return


# --- helpers ----------------------------------------------------------------


_TOKEN_RE = re.compile(r"\w+")
_STOPWORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can",
    "had", "has", "have", "her", "his", "its", "may", "one", "our", "out",
    "she", "two", "way", "who", "with", "this", "that", "from", "they",
    "them", "their", "there", "what", "when", "where", "which", "while",
    "would", "could", "should", "than", "then", "into", "your",
})


def _tokens(s: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(s.lower()) if len(t) > 2 and t not in _STOPWORDS}


def _find_related_pages(vault: Path, source_text: str, top_n: int) -> list[Path]:
    src_tokens = _tokens(source_text)
    if not src_tokens:
        return []
    wiki = vault / "wiki"
    scored: list[tuple[int, Path]] = []
    for p in wiki.rglob("*.md"):
        if p.name in {"index.md", "log.md"}:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        score = len(src_tokens & _tokens(text))
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:top_n]]


def _gather_existing_pages(vault: Path, analysis: str) -> list[Path]:
    seen: set[Path] = set()
    paths: list[Path] = []
    for m in re.finditer(r"wiki/[\w/\-.]+\.md", analysis):
        p = (vault / m.group(0)).resolve()
        if p in seen:
            continue
        seen.add(p)
        if p.is_file():
            paths.append(p)
    for name in ("index.md", "log.md"):
        p = (vault / "wiki" / name).resolve()
        if p not in seen and p.is_file():
            seen.add(p)
            paths.append(p)
    return paths


def _build_system(prompt_name: str, schema: str) -> str:
    return f"{load_prompt(prompt_name)}\n\n<schema>\n{schema}\n</schema>"


def _read_pages(paths: Iterable[Path], vault: Path, max_chars_per_page: int | None = None) -> str:
    parts: list[str] = []
    for p in paths:
        rel = p.relative_to(vault)
        content = p.read_text(encoding='utf-8')
        if max_chars_per_page is not None and len(content) > max_chars_per_page:
            content = content[:max_chars_per_page] + "\n\n[页面过长，已截断]"
        parts.append(f"--- {rel} ---\n{content}")
    return "\n\n".join(parts)


def _drive_meta_block(meta: DriveMeta) -> str:
    return (
        "<drive_meta>\n"
        f"file_id: {meta.file_id}\n"
        f"modified_time: {meta.modified_time}\n"
        f"web_view_link: {meta.web_view_link}\n"
        f"drive_path: {meta.drive_path}\n"
        f"mime_type: {meta.mime_type}\n"
        "</drive_meta>"
    )


def _github_meta_block(meta: GitHubMeta) -> str:
    return (
        "<github_meta>\n"
        f"owner: {meta.owner}\n"
        f"repo: {meta.repo}\n"
        f"branch: {meta.branch}\n"
        f"path: {meta.path}\n"
        f"blob_sha: {meta.blob_sha}\n"
        f"html_url: {meta.html_url}\n"
        "</github_meta>"
    )


def _granola_meta_block(meta: GranolaMeta) -> str:
    attendees = ", ".join(meta.attendees) if meta.attendees else ""
    folders = ", ".join(meta.folder_names) if meta.folder_names else ""
    return (
        "<granola_meta>\n"
        f"note_id: {meta.note_id}\n"
        f"created_at: {meta.created_at}\n"
        f"updated_at: {meta.updated_at}\n"
        f"web_url: {meta.web_url}\n"
        f"attendees: {attendees}\n"
        f"folders: {folders}\n"
        "</granola_meta>"
    )


def _external_meta_block(source: IngestSource) -> str:
    if source.drive_meta is not None:
        return _drive_meta_block(source.drive_meta)
    if source.granola_meta is not None:
        return _granola_meta_block(source.granola_meta)
    if source.github_meta is not None:
        return _github_meta_block(source.github_meta)
    return ""


def _build_stage1_user(
    *, index: str, related: list[Path], vault: Path, source: IngestSource
) -> str:
    MAX_SOURCE_CHARS = 6000  # Stage 1 可以稍大
    source_text = source.text
    if len(source_text) > MAX_SOURCE_CHARS:
        source_text = source_text[:MAX_SOURCE_CHARS] + "\n\n[文档过长，已截断]"
    # related_pages 也限制一下，每个 800 字符
    related_block = _read_pages(related, vault, max_chars_per_page=800) if related else "(none)"
    meta_block = _external_meta_block(source)
    return (
        f"<index>\n{index}\n</index>\n\n"
        f"<related_pages>\n{related_block}\n</related_pages>\n\n"
        f'<source filename="{source.filename}">\n{source_text}\n</source>'
        + (f"\n\n{meta_block}" if meta_block else "")
    )


def _build_stage2_user(
    *, analysis: str, vault: Path, source: IngestSource,
    existing: list[Path], today: str,
) -> str:
    # 1. 截断源文本 (保留 4000 字符，约 1000-2000 tokens)
    MAX_SOURCE_CHARS = 4000
    source_text = source.text
    if len(source_text) > MAX_SOURCE_CHARS:
        source_text = source_text[:MAX_SOURCE_CHARS] + "\n\n[文档过长，已截断]"

    # 2. 截断分析文本 (保留 3000 字符)
    MAX_ANALYSIS_CHARS = 3000
    analysis_text = analysis
    if len(analysis_text) > MAX_ANALYSIS_CHARS:
        analysis_text = analysis_text[:MAX_ANALYSIS_CHARS] + "\n\n[分析过长，已截断]"

    # 3. 限制现有页面：只取前 5 个，每个页面最多保留 1000 字符
    MAX_EXISTING_PAGES = 5
    MAX_PAGE_CHARS = 1000
    existing = existing[:MAX_EXISTING_PAGES]  # 只保留前 N 个
    existing_block = _read_pages(existing, vault, max_chars_per_page=MAX_PAGE_CHARS) if existing else "(none)"

    meta_block = _external_meta_block(source)
    return (
        f"<analysis>\n{analysis_text}\n</analysis>\n\n"
        f'<source filename="{source.filename}" hash="{source.hash}" '
        f'source_path="{source.source_path}">\n{source_text}\n</source>'
        + (f"\n\n{meta_block}" if meta_block else "")
        + f"\n\n<existing_pages>\n{existing_block}\n</existing_pages>\n\n"
        f"<today>{today}</today>"
    )
# def _build_stage2_user(
#     *, analysis: str, vault: Path, source: IngestSource,
#     existing: list[Path], today: str,
# ) -> str:
#     existing_block = _read_pages(existing, vault) if existing else "(none)"
#     meta_block = _external_meta_block(source)
#     return (
#         f"<analysis>\n{analysis}\n</analysis>\n\n"
#         f'<source filename="{source.filename}" hash="{source.hash}" '
#         f'source_path="{source.source_path}">\n{source.text}\n</source>'
#         + (f"\n\n{meta_block}" if meta_block else "")
#         + f"\n\n<existing_pages>\n{existing_block}\n</existing_pages>\n\n"
#         f"<today>{today}</today>"
#     )


def _fmt_usage(u: Usage) -> str:
    return f"in={u.input_tokens} out={u.output_tokens} cached={u.cached_input_tokens}"

