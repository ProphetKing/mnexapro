from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from mnexa.cli import app

runner = CliRunner()


def test_init_creates_vault(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    result = runner.invoke(app, ["init", str(vault)])
    assert result.exit_code == 0, result.output

    for d in ("raw", "wiki", "wiki/sources", "wiki/entities", "wiki/concepts", ".mnexa"):
        assert (vault / d).is_dir(), f"missing dir: {d}"

    for f in ("CLAUDE.md", ".gitignore", "wiki/index.md", "wiki/log.md"):
        assert (vault / f).is_file(), f"missing file: {f}"

    assert (vault / ".git").is_dir()

    log = (vault / "wiki" / "log.md").read_text()
    assert "INIT — vault created" in log

    gitignore = (vault / ".gitignore").read_text()
    assert ".mnexa/" in gitignore
    assert ".env" in gitignore

    head = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Initialize Mnexa vault" in head.stdout


def test_init_refuses_nonempty(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "x").write_text("hi")
    result = runner.invoke(app, ["init", str(vault)])
    assert result.exit_code != 0
    assert "not empty" in result.output


def test_init_into_empty_dir(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    result = runner.invoke(app, ["init", str(vault)])
    assert result.exit_code == 0, result.output
    assert (vault / "CLAUDE.md").is_file()
