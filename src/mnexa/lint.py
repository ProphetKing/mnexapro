"""Audit the wiki for structural and semantic issues.

Two tiers:
  1. Deterministic checks (no LLM): broken links, frontmatter validation,
     index consistency, orphan pages, slug style, ungrounded entity/concept
     pages.
  2. LLM check (one call): contradictions, staleness, missing pages,
     slug typos, biased framing.

Output: a markdown report at `.mnexa/lint/<timestamp>.md` plus a summary
to stderr.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
import yaml

from mnexa import storage
from mnexa.llm import LLMClient, get_client
from mnexa.parser import NO_FRONTMATTER_PATHS, REQUIRED_FIELDS
from mnexa.prompts import load as load_prompt


@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    page: Path | None
    message: str


_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]")
_MARKER_RE = re.compile(r"⟦\"[^\"⟧]*\"⟧")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_LLM_FINDING_RE = re.compile(
    r"^[\s\-*]*\*\*([\w-]+)\*\*\s*\[([^\]]+)\][:\s]+(.+?)\s*$",
    re.MULTILINE,
)


def run(*, fix: bool = False, client: LLMClient | None = None) -> None:
    if fix:
        typer.echo(
            "warning: --fix is not implemented in v0; running report-only",
            err=True,
        )
    asyncio.run(_run_async(client=client))


async def _run_async(*, client: LLMClient | None) -> None:
    vault = storage.find_vault(Path.cwd())
    if vault is None:
        typer.echo(
            "error: not inside an Mnexa vault (run `mnexa init` first)", err=True
        )
        raise typer.Exit(1)

    typer.echo("[lint] running deterministic checks…", err=True)
    findings: list[Finding] = list(_deterministic_checks(vault))

    if client is None:
        try:
            client = get_client()
        except RuntimeError as e:
            typer.echo(f"warning: skipping LLM checks ({e})", err=True)
            client = None

    if client is not None:
        typer.echo("[lint] running LLM checks…", err=True)
        try:
            findings.extend(await _llm_checks(vault, client))
        except (RuntimeError, OSError) as e:
            typer.echo(f"warning: LLM check failed ({e})", err=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = vault / ".mnexa" / "lint" / f"{timestamp}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(findings, timestamp), encoding="utf-8")

    n_err = sum(1 for f in findings if f.severity == "error")
    n_warn = sum(1 for f in findings if f.severity == "warning")
    n_info = sum(1 for f in findings if f.severity == "info")
    typer.echo(
        f"[lint] {n_err} errors · {n_warn} warnings · {n_info} info", err=True
    )
    typer.echo(f"[lint] report: {report_path.relative_to(vault)}", err=True)


def _deterministic_checks(vault: Path) -> list[Finding]:
    findings: list[Finding] = []
    wiki = vault / "wiki"
    all_pages = sorted(wiki.rglob("*.md"))

    page_slugs: set[str] = set()
    for p in all_pages:
        rel_in_wiki = p.relative_to(wiki).with_suffix("")
        page_slugs.add(str(rel_in_wiki))

    outbound: dict[Path, set[str]] = {}
    for p in all_pages:
        text = p.read_text(encoding="utf-8")
        outbound[p] = {m.group(1).strip() for m in _WIKILINK_RE.finditer(text)}

    # 1. Broken wikilinks (error)
    for p, links in outbound.items():
        for link in links:
            if link not in page_slugs:
                findings.append(
                    Finding(
                        severity="error",
                        check="broken-link",
                        page=p.relative_to(vault),
                        message=f"references [[{link}]] which does not exist",
                    )
                )

    # 2. Frontmatter validation (error)
    for p in all_pages:
        rel = p.relative_to(vault)
        if rel in NO_FRONTMATTER_PATHS:
            continue
        text = p.read_text(encoding="utf-8")
        fm = _read_frontmatter(text)
        if fm is None:
            findings.append(
                Finding(
                    severity="error",
                    check="frontmatter-missing",
                    page=rel,
                    message="missing or invalid frontmatter",
                )
            )
            continue
        type_ = fm.get("type")
        if not isinstance(type_, str) or type_ not in REQUIRED_FIELDS:
            findings.append(
                Finding(
                    severity="error",
                    check="frontmatter-type",
                    page=rel,
                    message=f"invalid or missing 'type' field: {type_!r}",
                )
            )
            continue
        missing = REQUIRED_FIELDS[type_] - fm.keys()
        if missing:
            findings.append(
                Finding(
                    severity="error",
                    check="frontmatter-fields",
                    page=rel,
                    message=f"missing required fields: {sorted(missing)}",
                )
            )

    # 3. Index/wiki consistency (error)
    index_path = wiki / "index.md"
    if index_path.is_file():
        index_text = index_path.read_text(encoding="utf-8")
        index_links = {
            m.group(1).strip() for m in _WIKILINK_RE.finditer(index_text)
        }
        for link in index_links:
            if link not in page_slugs:
                findings.append(
                    Finding(
                        severity="error",
                        check="index-stale",
                        page=Path("wiki/index.md"),
                        message=f"[[{link}]] does not resolve to a real page",
                    )
                )
        for slug in sorted(page_slugs):
            if slug in {"index", "log"}:
                continue
            if slug not in index_links:
                findings.append(
                    Finding(
                        severity="error",
                        check="index-missing",
                        page=Path(f"wiki/{slug}.md"),
                        message="page is not listed in wiki/index.md",
                    )
                )
    else:
        findings.append(
            Finding(
                severity="error",
                check="index-missing",
                page=None,
                message="wiki/index.md does not exist",
            )
        )

    # 4. Orphan pages (warning)
    inbound: dict[str, set[Path]] = {}
    for p, links in outbound.items():
        if p.relative_to(wiki) == Path("index.md"):
            continue
        for link in links:
            inbound.setdefault(link, set()).add(p)
    for slug in sorted(page_slugs):
        if slug in {"index", "log"}:
            continue
        if not inbound.get(slug):
            findings.append(
                Finding(
                    severity="warning",
                    check="orphan",
                    page=Path(f"wiki/{slug}.md"),
                    message="no inbound wikilinks (page is unreachable)",
                )
            )

    # 5. Ungrounded entity/concept pages (warning)
    for p in all_pages:
        rel = p.relative_to(vault)
        if rel in NO_FRONTMATTER_PATHS:
            continue
        text = p.read_text(encoding="utf-8")
        fm = _read_frontmatter(text)
        if not fm:
            continue
        type_ = fm.get("type")
        if type_ in {"entity", "concept"} and not _MARKER_RE.search(text):
            findings.append(
                Finding(
                    severity="warning",
                    check="ungrounded",
                    page=rel,
                    message=f"{type_} page has no source-quote markers",
                )
            )

    # 6. Slug style (info)
    for p in all_pages:
        if p.name in {"index.md", "log.md"}:
            continue
        if not _SLUG_RE.match(p.stem):
            findings.append(
                Finding(
                    severity="info",
                    check="slug-style",
                    page=p.relative_to(vault),
                    message=f"slug {p.stem!r} should be lowercase ASCII with hyphens",
                )
            )

    return findings


async def _llm_checks(vault: Path, client: LLMClient) -> list[Finding]:
    schema = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    pages_text = _gather_all_pages(vault)
    system = f"{load_prompt('lint.md')}\n\n<schema>\n{schema}\n</schema>"
    user = f"<pages>\n{pages_text}\n</pages>"
    completion = await client.complete(
        system=system, user=user, cache_system=True
    )
    return _parse_llm_findings(completion.text)


def _gather_all_pages(vault: Path) -> str:
    wiki = vault / "wiki"
    parts: list[str] = []
    for p in sorted(wiki.rglob("*.md")):
        rel = p.relative_to(vault)
        parts.append(f"--- {rel} ---\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _parse_llm_findings(text: str) -> list[Finding]:
    if "no issues found" in text.strip().lower()[:20]:
        return []
    findings: list[Finding] = []
    for m in _LLM_FINDING_RE.finditer(text):
        check, page_str, message = m.group(1), m.group(2).strip(), m.group(3).strip()
        page: Path | None = None if page_str == "*" else Path(page_str)
        findings.append(
            Finding(severity="warning", check=check, page=page, message=message)
        )
    return findings


def _read_frontmatter(text: str) -> dict[str, Any] | None:
    if not text.startswith("---\n"):
        return None
    rest = text[4:]
    end = rest.find("\n---\n")
    if end < 0:
        return None
    try:
        loaded: object = yaml.safe_load(rest[:end])
    except yaml.YAMLError:
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded  # type: ignore[return-value]


def _render_report(findings: list[Finding], timestamp: str) -> str:
    n_err = sum(1 for f in findings if f.severity == "error")
    n_warn = sum(1 for f in findings if f.severity == "warning")
    n_info = sum(1 for f in findings if f.severity == "info")

    lines = [
        "# Mnexa Lint Report",
        "",
        f"Generated: {timestamp}",
        "",
        f"Summary: {n_err} errors · {n_warn} warnings · {n_info} info",
        "",
    ]

    for severity, label in [
        ("error", "Errors"),
        ("warning", "Warnings"),
        ("info", "Info"),
    ]:
        section = [f for f in findings if f.severity == severity]
        if not section:
            continue
        lines.append(f"## {label}")
        lines.append("")
        for f in section:
            page_str = f"[{f.page}]" if f.page else "[*]"
            lines.append(f"- **{f.check}** {page_str} {f.message}")
        lines.append("")

    if not findings:
        lines.append("No issues found.")
        lines.append("")

    return "\n".join(lines)
