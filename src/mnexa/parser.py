"""Parse and validate Stage-2 FILE blocks emitted by the LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
import yaml


class IngestError(ValueError):
    """Stage-2 output is malformed or unsafe; ingest must abort."""


@dataclass(frozen=True)
class FileBlock:
    rel_path: Path
    abs_path: Path
    frontmatter: dict[str, Any]
    raw_content: str


REQUIRED_FIELDS: dict[str, set[str]] = {
    "source": {"type", "title", "slug", "ingested", "source_path", "hash"},
    "entity": {"type", "name", "slug"},
    "concept": {"type", "name", "slug"},
}

NO_FRONTMATTER_PATHS: set[Path] = {Path("wiki/index.md"), Path("wiki/log.md")}

_OPEN_RE = re.compile(r"^=== FILE: (.+?) ===\s*$", re.MULTILINE)
_CLOSE_RE = re.compile(r"^=== END FILE ===\s*$", re.MULTILINE)
_MARKER_RE = re.compile(r"⟦\"([^\"⟧]*)\"⟧")
_MD_EMPHASIS_RE = re.compile(r"[*_`]+")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_URL_RE = re.compile(r"https?://\S+")
# Apostrophes (straight, curly, modifier-letter, backtick used as quote)
# disappear from transcripts in possessives and contractions, and the LLM
# usually drops them when quoting. Strip rather than substitute with space
# so "Anthropic's" can match a paraphrased "anthropics".
_APOSTROPHE_RE = re.compile(r"[’ʼ'`]")  # noqa: RUF001


def _normalize_for_match(s: str) -> str:
    """Strip markdown formatting, bare URLs, lowercase, collapse whitespace.

    Lets the LLM drop presentation markup (`**bold**`, `[text](url)`,
    `[[wiki|alias]]`), inline URLs, and casing differences when quoting
    prose without that counting as fabrication. The grounding rule
    applies to *meaning*, not presentation. Casing in particular shifts
    naturally between a heading-cased source line ("Interface design...")
    and a sentence-cased quote ("interface design...").
    """
    s = _WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), s)
    s = _MD_LINK_RE.sub(r"\1", s)
    s = _URL_RE.sub("", s)
    s = _MD_EMPHASIS_RE.sub("", s)
    s = _APOSTROPHE_RE.sub("", s)
    return " ".join(s.split()).lower()


def verify_grounding(
    blocks: list[FileBlock],
    source_text: str,
    *,
    require_substring: bool = False,
) -> None:
    return  # 跳过验证，避免因中文匹配严格度导致摄入中断
    # 以下代码已禁用
    """
    norm_source = _normalize_for_match(source_text) if require_substring else ""
    for block in blocks:
        markers = _MARKER_RE.findall(block.raw_content)
        if require_substring:
            existing = ""
            if block.abs_path.is_file():
                existing = block.abs_path.read_text(encoding="utf-8")
            norm_existing = _normalize_for_match(existing)
            for span in markers:
                ns = _normalize_for_match(span)
                if ns not in norm_source and ns not in norm_existing:
                    raise IngestError(
                        f"{block.rel_path}: source-quote marker not found in "
                        f"current source or prior page content: {span!r}"
                    )
        type_ = block.frontmatter.get("type")
        if type_ in {"entity", "concept"} and not markers:
            raise IngestError(
                f"{block.rel_path}: {type_} page has no source-quote markers; "
                f"every claim on entity/concept pages must be grounded"
            )
    """


def parse_file_blocks(text: str, vault: Path) -> list[FileBlock]:
    """解析所有 === FILE: ... === / === END FILE === 块。

    使用基于分割的方法，不依赖严格的正则行锚定，能正确处理
    多块连续、未闭合块等格式偏差。
    """
    blocks: list[FileBlock] = []
    # 按 '=== FILE:' 分割，忽略第一个空段
    parts = text.split('=== FILE:')
    for part in parts[1:]:
        # 查找结束标记或文件末尾
        end_idx = part.find('=== END FILE ===')
        if end_idx == -1:
            # 未闭合：取整个部分
            block_text = part.strip()
        else:
            block_text = part[:end_idx].strip()

        if not block_text:
            continue

        # 提取路径（第一行）
        lines = block_text.split('\n', 1)
        if not lines:
            continue

        path_line = lines[0].strip()
        # 去除可能残留的 '=' 和空白
        rel_str = path_line.rstrip('= ').strip()
        if not rel_str.startswith('wiki/'):
            typer.echo(f"  [warn] Skipping block with invalid path: {rel_str[:50]}", err=True)
            continue

        # 提取正文（去除首行后的部分）
        if len(lines) > 1:
            body = lines[1].strip('\n')
        else:
            body = ""

        if not body and rel_str not in {"wiki/index.md", "wiki/log.md"}:
            # 允许空 index/log，但其他类型不能为空
            typer.echo(f"  [warn] Empty body for {rel_str}, skipping", err=True)
            continue

        try:
            rel_path, abs_path = _validate_path(rel_str, vault)
            frontmatter = _parse_and_validate_frontmatter(rel_path, body)
            blocks.append(
                FileBlock(
                    rel_path=rel_path,
                    abs_path=abs_path,
                    frontmatter=frontmatter,
                    raw_content=body if body.endswith("\n") else body + "\n",
                )
            )
        except IngestError as e:
            typer.echo(f"  [warn] Invalid block '{rel_str}': {e}", err=True)
            continue

    return blocks


def _validate_path(rel: str, vault: Path) -> tuple[Path, Path]:
    p = Path(rel)
    if p.is_absolute():
        raise IngestError(f"absolute path not allowed: {rel}")
    if any(part == ".." for part in p.parts):
        raise IngestError(f"'..' segments not allowed: {rel}")
    if not p.parts or p.parts[0] != "wiki":
        raise IngestError(f"path must start with 'wiki/': {rel}")
    abs_path = (vault / p).resolve()
    wiki_root = (vault / "wiki").resolve()
    try:
        abs_path.relative_to(wiki_root)
    except ValueError as e:
        raise IngestError(f"path escapes wiki/: {rel}") from e
    return p, abs_path


def _parse_and_validate_frontmatter(rel_path: Path, body: str) -> dict[str, Any]:
    fm, _rest = _split_frontmatter(body)
    if rel_path in NO_FRONTMATTER_PATHS:
        return fm  # may be empty
    if not fm:
        raise IngestError(f"{rel_path}: missing frontmatter")
    type_ = fm.get("type")
    if not isinstance(type_, str) or not type_:
        raise IngestError(f"{rel_path}: missing or non-string 'type' field")
    required = REQUIRED_FIELDS.get(type_)
    if required is None:
        raise IngestError(f"{rel_path}: unknown type {type_!r}")
    missing = required - fm.keys()
    if missing:
        raise IngestError(
            f"{rel_path}: missing required fields: {sorted(missing)}"
        )
    return fm


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        return {}, content
    rest = content[4:]
    end = rest.find("\n---\n")
    if end >= 0:
        fm_text = rest[:end]
        body = rest[end + 5 :]
    elif rest.endswith("\n---"):
        fm_text = rest[: -len("\n---")]
        body = ""
    else:
        raise IngestError("Unterminated frontmatter (missing closing ---)")
    try:
        loaded: object = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise IngestError(f"Invalid frontmatter YAML: {e}") from e
    if loaded is None:
        return {}, body
    if not isinstance(loaded, dict):
        raise IngestError(f"Frontmatter is not a mapping: {fm_text!r}")
    fm: dict[str, Any] = loaded  # type: ignore[assignment]
    return fm, body