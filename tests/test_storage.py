from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnexa import storage
from mnexa.cli import app

runner = CliRunner()


def _init_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "v"
    result = runner.invoke(app, ["init", str(vault)])
    assert result.exit_code == 0
    return vault


def test_find_vault_walks_up(tmp_path: Path) -> None:
    vault = _init_vault(tmp_path)
    nested = vault / "wiki" / "sources"
    assert storage.find_vault(nested) == vault


def test_find_vault_returns_none_outside(tmp_path: Path) -> None:
    assert storage.find_vault(tmp_path) is None


def test_write_pages_creates_files(tmp_path: Path) -> None:
    vault = _init_vault(tmp_path)
    pages = {
        vault / "wiki" / "sources" / "foo.md": "hello\n",
        vault / "wiki" / "entities" / "bar.md": "world\n",
    }
    storage.write_pages(vault, pages)
    assert (vault / "wiki" / "sources" / "foo.md").read_text() == "hello\n"
    assert (vault / "wiki" / "entities" / "bar.md").read_text() == "world\n"


def test_write_pages_overwrites(tmp_path: Path) -> None:
    vault = _init_vault(tmp_path)
    target = vault / "wiki" / "index.md"
    storage.write_pages(vault, {target: "# new\n"})
    assert target.read_text() == "# new\n"


def test_write_pages_no_op_on_empty(tmp_path: Path) -> None:
    vault = _init_vault(tmp_path)
    storage.write_pages(vault, {})


def test_write_pages_cleans_staging(tmp_path: Path) -> None:
    vault = _init_vault(tmp_path)
    target = vault / "wiki" / "sources" / "x.md"
    storage.write_pages(vault, {target: "x\n"})
    staging_root = vault / ".mnexa" / "staging"
    assert not staging_root.exists() or not any(staging_root.iterdir())


def test_write_pages_aborts_on_invalid_target(tmp_path: Path) -> None:
    vault = _init_vault(tmp_path)
    outside = tmp_path / "outside.md"
    with pytest.raises(ValueError):
        storage.write_pages(vault, {outside: "x"})


def test_git_commit_creates_commit(tmp_path: Path) -> None:
    vault = _init_vault(tmp_path)
    storage.write_pages(vault, {vault / "wiki" / "sources" / "x.md": "x\n"})
    assert storage.git_commit(vault, "ingest: x") is True
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True, check=True
    )
    assert "ingest: x" in log.stdout


def test_git_commit_no_changes_returns_false(tmp_path: Path) -> None:
    vault = _init_vault(tmp_path)
    assert storage.git_commit(vault, "noop") is False


def test_git_rollback_restores_tracked_and_removes_untracked(tmp_path: Path) -> None:
    vault = _init_vault(tmp_path)
    # Modify a tracked file.
    (vault / "wiki" / "index.md").write_text("# tampered\n")
    # Add an untracked file.
    (vault / "wiki" / "sources" / "stray.md").write_text("stray\n")

    storage.git_rollback(vault)

    assert "Sources" in (vault / "wiki" / "index.md").read_text()
    assert not (vault / "wiki" / "sources" / "stray.md").exists()
