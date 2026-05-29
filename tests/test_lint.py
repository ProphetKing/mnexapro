from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from mnexa import lint as lint_mod
from mnexa.cli import app
from tests.fakes import FakeLLMClient

runner = CliRunner()


def _init(tmp_path: Path) -> Path:
    vault = tmp_path / "v"
    result = runner.invoke(app, ["init", str(vault)])
    assert result.exit_code == 0
    return vault


def _write(vault: Path, rel: str, content: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _findings_by_check(report_text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in report_text.splitlines():
        line = line.strip()
        if not line.startswith("- **"):
            continue
        end = line.find("**", 4)
        if end < 0:
            continue
        check = line[4:end]
        out.setdefault(check, []).append(line)
    return out


def test_clean_vault_has_no_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _init(tmp_path)
    monkeypatch.chdir(vault)
    fake = FakeLLMClient(analysis="No issues found.", generation="")
    lint_mod.run(client=fake)

    reports = list((vault / ".mnexa" / "lint").iterdir())
    assert len(reports) == 1
    text = reports[0].read_text()
    assert "No issues found" in text


def test_broken_link_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _init(tmp_path)
    _write(
        vault, "wiki/concepts/foo.md",
        "---\ntype: concept\nname: Foo\nslug: foo\n---\n\n"
        'Foo refers to [[entities/missing]] ⟦"x"⟧.\n\n'
        "**Discussed in**\n- [[sources/none]]\n",
    )
    monkeypatch.chdir(vault)
    fake = FakeLLMClient(analysis="No issues found.", generation="")
    lint_mod.run(client=fake)

    text = next((vault / ".mnexa" / "lint").iterdir()).read_text()
    findings = _findings_by_check(text)
    assert "broken-link" in findings
    # The page wiki/concepts/foo.md links to entities/missing AND sources/none
    assert any("entities/missing" in line for line in findings["broken-link"])


def test_index_missing_page_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _init(tmp_path)
    _write(
        vault, "wiki/entities/foo.md",
        '---\ntype: entity\nname: Foo\nslug: foo\n---\n\nA foo ⟦"a foo"⟧.\n',
    )
    # Don't add to index.md
    monkeypatch.chdir(vault)
    fake = FakeLLMClient(analysis="No issues found.", generation="")
    lint_mod.run(client=fake)

    text = next((vault / ".mnexa" / "lint").iterdir()).read_text()
    findings = _findings_by_check(text)
    assert "index-missing" in findings


def test_orphan_page_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _init(tmp_path)
    _write(
        vault, "wiki/entities/lonely.md",
        '---\ntype: entity\nname: Lonely\nslug: lonely\n---\n\nNo links ⟦"alone"⟧.\n',
    )
    # Add it to index so the index-missing check passes
    _write(
        vault, "wiki/index.md",
        "# Index\n\n## Entities\n- [[entities/lonely]] — A page with no inbound links.\n",
    )
    monkeypatch.chdir(vault)
    fake = FakeLLMClient(analysis="No issues found.", generation="")
    lint_mod.run(client=fake)

    text = next((vault / ".mnexa" / "lint").iterdir()).read_text()
    findings = _findings_by_check(text)
    assert "orphan" in findings


def test_ungrounded_entity_page_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _init(tmp_path)
    _write(
        vault, "wiki/entities/foo.md",
        "---\ntype: entity\nname: Foo\nslug: foo\n---\n\nNo markers here.\n",
    )
    _write(
        vault, "wiki/index.md",
        "# Index\n\n## Entities\n- [[entities/foo]] — A foo.\n",
    )
    monkeypatch.chdir(vault)
    fake = FakeLLMClient(analysis="No issues found.", generation="")
    lint_mod.run(client=fake)

    text = next((vault / ".mnexa" / "lint").iterdir()).read_text()
    findings = _findings_by_check(text)
    assert "ungrounded" in findings


def test_llm_findings_parsed_into_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _init(tmp_path)
    monkeypatch.chdir(vault)

    canned_llm = """- **slug-typo** [wiki/entities/caufield.md] should be 'caulfield'
- **missing-page** [*] entity 'Joel Hooks' is mentioned in 2 sources but has no page
"""
    fake = FakeLLMClient(analysis=canned_llm, generation="")
    lint_mod.run(client=fake)

    text = next((vault / ".mnexa" / "lint").iterdir()).read_text()
    findings = _findings_by_check(text)
    assert "slug-typo" in findings
    assert "missing-page" in findings


def test_lint_outside_vault_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    fake = FakeLLMClient(analysis="", generation="")
    with pytest.raises(typer.Exit):
        lint_mod.run(client=fake)


def test_fix_flag_warns_but_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _init(tmp_path)
    monkeypatch.chdir(vault)
    fake = FakeLLMClient(analysis="No issues found.", generation="")
    lint_mod.run(fix=True, client=fake)
    err = capsys.readouterr().err
    assert "--fix is not implemented" in err
