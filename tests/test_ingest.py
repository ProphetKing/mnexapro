from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from mnexa import ingest as ingest_mod
from mnexa.cli import app
from tests.fakes import FakeLLMClient

runner = CliRunner()


def _init(tmp_path: Path) -> Path:
    vault = tmp_path / "v"
    result = runner.invoke(app, ["init", str(vault)])
    assert result.exit_code == 0, result.output
    return vault


CANNED_ANALYSIS = """## 1. Source

- **Title**: Karpathy LLM Wiki Gist
- **Proposed slug**: karpathy-llm-wiki-gist
- **One-line description**: A pattern for an LLM-maintained personal wiki.

## 2. Main claims

- A persistent wiki, not RAG, can compound knowledge.

## 3. Entities

- **andrej-karpathy** — Andrej Karpathy — status: new

## 4. Concepts

- **llm-wiki** — LLM-maintained Wiki — status: new
"""


CANNED_GENERATION = """=== FILE: wiki/sources/karpathy-llm-wiki-gist.md ===
---
type: source
title: Karpathy LLM Wiki Gist
slug: karpathy-llm-wiki-gist
ingested: 2026-04-27
source_path: raw/karpathy.md
hash: HASH
---

# Summary

A pattern for an LLM-maintained personal wiki.

# Key claims

- A persistent wiki can compound knowledge.

# Entities mentioned

- [[entities/andrej-karpathy]]

# Concepts mentioned

- [[concepts/llm-wiki]]
=== END FILE ===

=== FILE: wiki/entities/andrej-karpathy.md ===
---
type: entity
name: Andrej Karpathy
slug: andrej-karpathy
aliases: []
---

Author of an LLM-maintained wiki ⟦"LLM-maintained wiki"⟧.

**Mentioned in**

- [[sources/karpathy-llm-wiki-gist]]
=== END FILE ===

=== FILE: wiki/concepts/llm-wiki.md ===
---
type: concept
name: LLM-maintained Wiki
slug: llm-wiki
---

A pattern for an LLM-maintained wiki ⟦"LLM-maintained wiki"⟧.

**Discussed in**

- [[sources/karpathy-llm-wiki-gist]]
=== END FILE ===

=== FILE: wiki/index.md ===
# Index

## Sources
- [[sources/karpathy-llm-wiki-gist]] — A pattern for an LLM-maintained personal wiki.

## Entities
- [[entities/andrej-karpathy]] — Author of the gist.

## Concepts
- [[concepts/llm-wiki]] — Persistent wiki pattern.
=== END FILE ===

=== FILE: wiki/log.md ===
# Log

- 2026-04-27 INIT — vault created
- 2026-04-27 INGEST sources/karpathy-llm-wiki-gist — added
=== END FILE ===
"""


def test_ingest_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _init(tmp_path)
    src = tmp_path / "karpathy.md"
    src.write_text("# Karpathy LLM Wiki\n\nA pattern for an LLM-maintained wiki.\n")

    monkeypatch.chdir(vault)

    fake = FakeLLMClient(CANNED_ANALYSIS, CANNED_GENERATION)
    ingest_mod.run(src, client=fake)

    assert (vault / "wiki" / "sources" / "karpathy-llm-wiki-gist.md").is_file()
    assert (vault / "wiki" / "entities" / "andrej-karpathy.md").is_file()
    assert (vault / "wiki" / "concepts" / "llm-wiki.md").is_file()
    assert (vault / "raw" / "karpathy.md").is_file()

    index = (vault / "wiki" / "index.md").read_text()
    assert "karpathy-llm-wiki-gist" in index

    log = (vault / "wiki" / "log.md").read_text()
    assert "INGEST sources/karpathy-llm-wiki-gist" in log

    out = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault,
        capture_output=True, text=True, check=True,
    )
    assert "ingest: karpathy.md" in out.stdout


def test_ingest_outside_vault_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "stray.md"
    src.write_text("hi")
    fake = FakeLLMClient("", "")
    with pytest.raises(typer.Exit):
        ingest_mod.run(src, client=fake)


def test_ingest_no_blocks_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _init(tmp_path)
    src = tmp_path / "empty.md"
    src.write_text("nothing interesting")
    monkeypatch.chdir(vault)

    fake = FakeLLMClient(CANNED_ANALYSIS, "")
    ingest_mod.run(src, client=fake)

    out = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault,
        capture_output=True, text=True, check=True,
    )
    assert "ingest:" not in out.stdout
