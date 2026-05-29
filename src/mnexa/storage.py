"""Vault filesystem and git operations."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any
from uuid import uuid4

import typer
import yaml

VAULT_DIRS = (
    "raw",
    "wiki",
    "wiki/sources",
    "wiki/entities",
    "wiki/concepts",
    ".mnexa",
)


def init_vault(path: Path) -> None:
    path = path.expanduser().resolve()

    if path.exists() and any(path.iterdir()):
        typer.echo(f"error: {path} exists and is not empty", err=True)
        raise typer.Exit(1)

    if not _has_git():
        typer.echo("error: git is not installed or not on PATH", err=True)
        raise typer.Exit(1)

    path.mkdir(parents=True, exist_ok=True)

    for d in VAULT_DIRS:
        (path / d).mkdir(parents=True, exist_ok=True)

    (path / "CLAUDE.md").write_text(_template("CLAUDE.md"), encoding="utf-8")
    (path / ".gitignore").write_text(_template("gitignore"), encoding="utf-8")
    (path / "wiki" / "index.md").write_text(_template("index.md"), encoding="utf-8")
    (path / "wiki" / "log.md").write_text(
        f"# Log\n\n- {date.today().isoformat()} INIT — vault created\n",
        encoding="utf-8",
    )
    for d in VAULT_DIRS:
        dir_path = path / d
        dir_path.mkdir(parents=True, exist_ok=True)
        # 添加 .gitkeep 以防止空目录被 git clean 删除
        keep_file = dir_path / ".gitkeep"
        if not keep_file.exists():
            keep_file.touch()
    git(path, "init", "--quiet", "--initial-branch=main")
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "Initialize Mnexa vault")

    typer.echo(f"Initialized vault at {path}")


def read_frontmatter(path: Path) -> dict[str, Any]:
    """Read YAML frontmatter from a markdown file. Returns {} if absent or invalid."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    try:
        loaded: object = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded  # type: ignore[return-value]


def find_vault(start: Path) -> Path | None:
    """Walk up from `start` looking for a directory that looks like a vault."""
    p = start.expanduser().resolve()
    for candidate in (p, *p.parents):
        if (
            (candidate / "CLAUDE.md").is_file()
            and (candidate / "wiki").is_dir()
            and (candidate / ".git").is_dir()
        ):
            return candidate
    return None


def write_pages(vault: Path, pages: dict[Path, str]) -> None:
    """Atomically (best-effort) write multiple pages.

    All content is staged under .mnexa/staging/<uuid>/, fsynced, then
    os.replace'd into place. If the loop fails partway, files already moved
    are in the worktree — caller should `git_rollback(vault)` to recover.
    """
    if not pages:
        return

    staging = vault / ".mnexa" / "staging" / uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    try:
        moves: list[tuple[Path, Path]] = []
        for target, content in pages.items():
            rel = target.relative_to(vault)
            staged = staging / rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text(content, encoding="utf-8")
            with staged.open("rb") as f:
                os.fsync(f.fileno())
            moves.append((staged, target))

        for staged, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def git_rollback(vault: Path) -> None:
    """Roll back any uncommitted changes under wiki/.

    Restores tracked files and removes any untracked ones. The previous
    commit becomes the source of truth again.
    """
    git(vault, "checkout", "--quiet", "HEAD", "--", "wiki")
    git(vault, "clean", "-fdq", "wiki")


def git_commit(vault: Path, message: str) -> bool:
    """Stage and commit changes. Returns True if a commit was made."""
    git(vault, "add", ".")
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=vault,
        check=False,
    )
    if status.returncode == 0:
        return False
    git(vault, "commit", "--quiet", "-m", message)
    return True


def _template(name: str) -> str:
    return resources.files("mnexa.templates").joinpath(name).read_text(encoding="utf-8")


def _has_git() -> bool:
    try:
        subprocess.run(
            ["git", "--version"],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


def git(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        typer.echo(f"error: git {' '.join(args)} failed:\n{result.stderr}", err=True)
        raise typer.Exit(1)
