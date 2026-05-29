from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from mnexa import query as query_mod
from mnexa.cli import app
from tests.fakes import FakeLLMClient

runner = CliRunner()


def _init(tmp_path: Path) -> Path:
    vault = tmp_path / "v"
    result = runner.invoke(app, ["init", str(vault)])
    assert result.exit_code == 0
    return vault


def _seed_page(vault: Path, rel: str, content: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=vault, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"seed {rel}"], cwd=vault, check=True)


def test_query_streams_answer_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _init(tmp_path)
    _seed_page(
        vault, "wiki/concepts/rag.md",
        "---\ntype: concept\nname: RAG\nslug: rag\n---\n\n"
        "Retrieval-Augmented Generation retrieves chunks at query time.\n",
    )
    monkeypatch.chdir(vault)

    answer = "RAG retrieves document chunks at query time [[concepts/rag]]."
    fake = FakeLLMClient(analysis="", generation=answer)
    query_mod.run("What is RAG?", client=fake)

    out = capsys.readouterr().out
    assert "RAG retrieves" in out
    assert "[[concepts/rag]]" in out

    log = (vault / "wiki" / "log.md").read_text()
    assert 'QUERY "What is RAG?" → concepts/rag' in log

    git_log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault,
        capture_output=True, text=True, check=True,
    )
    assert 'query: "What is RAG?"' in git_log.stdout


def test_query_outside_vault_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    fake = FakeLLMClient(analysis="", generation="x")
    with pytest.raises(typer.Exit):
        query_mod.run("anything", client=fake)


def test_query_with_no_matching_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _init(tmp_path)
    monkeypatch.chdir(vault)

    # The fake doesn't actually read inputs; here we just verify no crash on empty.
    fake = FakeLLMClient(analysis="", generation="No relevant content in the wiki.")
    query_mod.run("totally unknown topic xyz", client=fake)

    out = capsys.readouterr().out
    assert "No relevant content" in out

    log = (vault / "wiki" / "log.md").read_text()
    assert "QUERY" in log
