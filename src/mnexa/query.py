"""Single-shot query against the wiki with [[wikilink]] citations."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import typer

from mnexa import storage
from mnexa.llm import LLMClient, Usage, get_client
from mnexa.prompts import load as load_prompt

MAX_PAGES = 10

_TOKEN_RE = re.compile(r"\w+")
_STOPWORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can",
    "had", "has", "have", "her", "his", "its", "may", "one", "our", "out",
    "she", "two", "way", "who", "with", "this", "that", "from", "they",
    "them", "their", "there", "what", "when", "where", "which", "while",
    "would", "could", "should", "than", "then", "into", "your", "how",
    "why", "does", "about",
})
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]")


def run(question: str, *, client: LLMClient | None = None) -> None:
    asyncio.run(_run_async(question, client=client))


async def _run_async(question: str, *, client: LLMClient | None) -> None:
    vault = storage.find_vault(Path.cwd())
    if vault is None:
        typer.echo(
            "error: not inside an Mnexa vault (run `mnexa init` first)", err=True
        )
        raise typer.Exit(1)

    if client is None:
        client = get_client()

    schema = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    index = (vault / "wiki" / "index.md").read_text(encoding="utf-8")
    pages = _find_pages(vault, question, MAX_PAGES)

    typer.echo(f"[query] searching {len(pages)} relevant pages…", err=True)

    system = f"{load_prompt('query.md')}\n\n<schema>\n{schema}\n</schema>"
    user = _build_user(question=question, index=index, pages=pages, vault=vault)

    accumulated: list[str] = []
    async for chunk in client.stream(system=system, user=user, cache_system=True):
        sys.stdout.write(chunk)
        sys.stdout.flush()
        accumulated.append(chunk)
    sys.stdout.write("\n")

    answer = "".join(accumulated)
    cited = _extract_wikilinks(answer)
    today = date.today().isoformat()
    q_short = question if len(question) <= 80 else question[:77] + "..."
    cited_str = ", ".join(cited) if cited else "(none)"

    log_path = vault / "wiki" / "log.md"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f'- {today} QUERY "{q_short}" → {cited_str}\n')

    storage.git_commit(vault, f'query: "{q_short}"')

    if client.last_usage is not None:
        typer.echo(f"[query] {_fmt_usage(client.last_usage)}", err=True)


def _tokens(s: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(s.lower()) if len(t) > 2 and t not in _STOPWORDS}


def _find_pages(vault: Path, question: str, top_n: int) -> list[Path]:
    q_tokens = _tokens(question)
    if not q_tokens:
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
        score = len(q_tokens & _tokens(text))
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:top_n]]


def _extract_wikilinks(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _WIKILINK_RE.finditer(text):
        target = m.group(1).strip()
        if target not in seen:
            seen.add(target)
            out.append(target)
    return out


def _read_pages(paths: Iterable[Path], vault: Path) -> str:
    parts: list[str] = []
    for p in paths:
        rel = p.relative_to(vault)
        parts.append(f"--- {rel} ---\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _build_user(*, question: str, index: str, pages: list[Path], vault: Path) -> str:
    pages_block = _read_pages(pages, vault) if pages else "(no relevant pages found)"
    return (
        f"<question>\n{question}\n</question>\n\n"
        f"<index>\n{index}\n</index>\n\n"
        f"<pages>\n{pages_block}\n</pages>"
    )


def _fmt_usage(u: Usage) -> str:
    return f"in={u.input_tokens} out={u.output_tokens} cached={u.cached_input_tokens}"
