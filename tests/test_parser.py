from __future__ import annotations

from pathlib import Path

import pytest

from mnexa.parser import IngestError, parse_file_blocks, verify_grounding


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    return tmp_path


def test_parses_source_and_index_blocks(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: wiki/sources/foo.md ===
---
type: source
title: Foo
slug: foo
ingested: 2026-04-27
source_path: raw/foo.md
hash: abc123
---

# Summary

Hello.
=== END FILE ===

some chatter the parser should ignore

=== FILE: wiki/index.md ===
# Index

## Sources
- [[sources/foo]] — A foo
=== END FILE ===
"""
    blocks = parse_file_blocks(text, vault)
    assert len(blocks) == 2
    assert blocks[0].rel_path == Path("wiki/sources/foo.md")
    assert blocks[0].frontmatter["slug"] == "foo"
    assert "Hello." in blocks[0].raw_content
    assert blocks[1].rel_path == Path("wiki/index.md")
    assert blocks[1].frontmatter == {}


def test_rejects_path_outside_wiki(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: raw/secret.md ===
hi
=== END FILE ===
"""
    with pytest.raises(IngestError, match="must start with 'wiki/'"):
        parse_file_blocks(text, vault)


def test_rejects_dotdot(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: wiki/../etc/passwd ===
hi
=== END FILE ===
"""
    with pytest.raises(IngestError, match=r"'\.\.' segments not allowed"):
        parse_file_blocks(text, vault)


def test_rejects_absolute(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: /etc/passwd ===
hi
=== END FILE ===
"""
    with pytest.raises(IngestError, match="absolute path not allowed"):
        parse_file_blocks(text, vault)


def test_rejects_missing_required_field(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: wiki/sources/foo.md ===
---
type: source
title: Foo
slug: foo
---
body
=== END FILE ===
"""
    with pytest.raises(IngestError, match="missing required fields"):
        parse_file_blocks(text, vault)


def test_rejects_unknown_type(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: wiki/sources/foo.md ===
---
type: bogus
slug: foo
---
body
=== END FILE ===
"""
    with pytest.raises(IngestError, match="unknown type"):
        parse_file_blocks(text, vault)


def test_rejects_missing_frontmatter_for_typed_page(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: wiki/entities/foo.md ===
just a body
=== END FILE ===
"""
    with pytest.raises(IngestError, match="missing frontmatter"):
        parse_file_blocks(text, vault)


def test_rejects_unterminated_block(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: wiki/index.md ===
# Index
"""
    with pytest.raises(IngestError, match="Unterminated FILE block"):
        parse_file_blocks(text, vault)


def test_rejects_unterminated_frontmatter(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: wiki/concepts/foo.md ===
---
type: concept
name: Foo
slug: foo
=== END FILE ===
"""
    with pytest.raises(IngestError, match="Unterminated frontmatter"):
        parse_file_blocks(text, vault)


def test_log_md_no_frontmatter_required(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: wiki/log.md ===
# Log

- 2026-04-27 INIT — vault created
- 2026-04-27 INGEST sources/foo — added
=== END FILE ===
"""
    blocks = parse_file_blocks(text, vault)
    assert len(blocks) == 1
    assert blocks[0].frontmatter == {}


def test_empty_input_yields_no_blocks(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    assert parse_file_blocks("", vault) == []
    assert parse_file_blocks("just some chatter", vault) == []


SOURCE = "Vannevar Bush's Memex (1945) — a personal, curated knowledge store."


def _entity_block(body: str) -> str:
    return f"""=== FILE: wiki/entities/vb.md ===
---
type: entity
name: Vannevar Bush
slug: vb
aliases: []
---

{body}

**Mentioned in**

- [[sources/foo]]
=== END FILE ===
"""


def test_grounding_passes_with_valid_markers(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = _entity_block(
        'Bush proposed the Memex in 1945 ⟦"Vannevar Bush\'s Memex (1945)"⟧.'
    )
    blocks = parse_file_blocks(text, vault)
    verify_grounding(blocks, SOURCE)  # does not raise


def test_grounding_fails_on_unknown_span(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = _entity_block(
        'Bush was an American engineer ⟦"American engineer"⟧.'
    )
    blocks = parse_file_blocks(text, vault)
    with pytest.raises(IngestError, match="not found in"):
        verify_grounding(blocks, SOURCE)


def test_grounding_requires_marker_on_entity(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = _entity_block("Bush proposed the Memex.")
    blocks = parse_file_blocks(text, vault)
    with pytest.raises(IngestError, match="no source-quote markers"):
        verify_grounding(blocks, SOURCE)


def test_grounding_skips_index_and_log(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = """=== FILE: wiki/index.md ===
# Index
=== END FILE ===

=== FILE: wiki/log.md ===
# Log
=== END FILE ===
"""
    blocks = parse_file_blocks(text, vault)
    verify_grounding(blocks, SOURCE)  # does not raise


def test_grounding_skips_substring_check_when_not_required(tmp_path: Path) -> None:
    """For transcript-style sources, marker text doesn't need to be a substring."""
    vault = _vault(tmp_path)
    text = _entity_block(
        'Bush invented all sorts of things ⟦"invented all sorts of things"⟧.'
    )
    blocks = parse_file_blocks(text, vault)
    # That span isn't in SOURCE — but with require_substring=False, allowed.
    verify_grounding(blocks, SOURCE, require_substring=False)


def test_grounding_still_requires_marker_when_substring_check_off(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    text = _entity_block("Bush proposed the Memex.")  # no marker
    blocks = parse_file_blocks(text, vault)
    with pytest.raises(IngestError, match="no source-quote markers"):
        verify_grounding(blocks, SOURCE, require_substring=False)


def test_grounding_accepts_marker_from_prior_page_content(tmp_path: Path) -> None:
    """Re-ingest preserves markers grounded by previous source."""
    vault = _vault(tmp_path)
    # Pre-existing page with a marker grounded by a prior source.
    prior_page = vault / "wiki" / "entities" / "vb.md"
    prior_page.write_text(
        '---\ntype: entity\nname: Vannevar Bush\nslug: vb\n---\n\n'
        'Bush worked at MIT ⟦"worked at MIT"⟧.\n'
    )
    # New source does NOT mention MIT.
    new_source = "Vannevar Bush proposed the Memex."
    text = _entity_block(
        'Bush worked at MIT ⟦"worked at MIT"⟧ and proposed the Memex '
        '⟦"proposed the Memex"⟧.'
    )
    # Override the path to point to the existing file
    text = text.replace("wiki/entities/vb.md", "wiki/entities/vb.md")
    blocks = parse_file_blocks(text, vault)
    verify_grounding(blocks, new_source)  # both markers verify (one via prior content)
