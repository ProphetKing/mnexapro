from __future__ import annotations

from typer.testing import CliRunner

from mnexa.cli import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "mnexa" in result.stdout.lower()


def test_subcommands_listed() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "ingest", "query", "lint"):
        assert cmd in result.stdout
