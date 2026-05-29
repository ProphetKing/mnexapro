from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer

from mnexa import ingest as ingest_mod
from mnexa.ingest import classify_target


def test_classify_granola_note_id_bare() -> None:
    t = classify_target("not_1d3tmYTlCICgjy")
    assert t.kind == "granola-note"
    assert t.external_id == "not_1d3tmYTlCICgjy"


def test_classify_granola_note_scheme() -> None:
    t = classify_target("granola://note/not_1d3tmYTlCICgjy")
    assert t.kind == "granola-note"
    assert t.external_id == "not_1d3tmYTlCICgjy"


def test_classify_granola_share_url_rejected_with_hint() -> None:
    with pytest.raises(typer.BadParameter, match="note ID"):
        classify_target("https://notes.granola.ai/d/f3e45e0f-24cc-480b-9a6c-8b1f5e3d7a2c")


def test_classify_granola_list_bare() -> None:
    t = classify_target("granola")
    assert t.kind == "granola-list"
    assert t.since is None


def test_classify_granola_list_with_since() -> None:
    t = classify_target("granola://since/2026-04-01")
    assert t.kind == "granola-list"
    assert t.since == "2026-04-01"


def test_classify_granola_unknown_form_errors() -> None:
    with pytest.raises(typer.BadParameter):
        classify_target("granola://wat/123")


def test_load_granola_source_renders_text() -> None:
    """Verify _load_granola_source flattens a note into IngestSource cleanly."""
    from mnexa.granola.client import GranolaNote, GranolaUser

    note = GranolaNote(
        note_id="not_1d3tmYTlCICgjy",
        title="Design review",
        created_at="2026-04-15T14:00:00Z",
        updated_at="2026-04-15T15:30:00Z",
        owner=GranolaUser(name="Alice Smith", email="alice@example.com"),
        web_url="https://notes.granola.ai/d/abc-123",
        summary_text="Decided to ship the feature next week.",
        summary_markdown="## Decision\n\nShip next week.",
        transcript=[
            {"speaker": {"source": "microphone", "diarization_label": "Speaker A"},
             "text": "Let's ship it."},
            {"speaker": {"source": "speaker"}, "text": "Agreed."},
        ],
        attendees=[
            GranolaUser(name="Alice Smith", email="alice@example.com"),
            GranolaUser(name="Bob Jones", email="bob@example.com"),
        ],
        folder_names=["Engineering"],
        raw={},
    )
    fake_client = MagicMock()
    fake_client.get_note.return_value = note

    source = ingest_mod._load_granola_source("not_1d3tmYTlCICgjy", fake_client)  # pyright: ignore[reportPrivateUsage]

    assert source.filename == "Design review"
    assert "Ship next week" in source.text  # uses markdown summary preferentially
    assert "Speaker A: Let's ship it." in source.text
    assert "speaker: Agreed." in source.text
    assert source.source_path == "granola://not_1d3tmYTlCICgjy"
    assert source.granola_meta is not None
    assert source.granola_meta.note_id == "not_1d3tmYTlCICgjy"
    assert source.granola_meta.attendees == ["Alice Smith", "Bob Jones"]
    assert source.granola_meta.folder_names == ["Engineering"]
    assert source.granola_meta.web_url == "https://notes.granola.ai/d/abc-123"
    assert source.granola_meta.updated_at == "2026-04-15T15:30:00Z"


def test_render_note_merges_consecutive_same_speaker_turns() -> None:
    """Granola splits utterances at silence boundaries; merging them lets the
    substring verifier match quotes that span turn boundaries."""
    from mnexa.granola.client import GranolaNote, GranolaUser, render_note_text

    note = GranolaNote(
        note_id="not_xxxxxxxxxxxxxx",
        title="Standup",
        created_at="", updated_at="",
        owner=GranolaUser(name=None, email=""),
        web_url="", summary_text="", summary_markdown=None,
        transcript=[
            {"speaker": {"source": "microphone"},
             "text": "trying to improve the quality"},
            {"speaker": {"source": "microphone"},
             "text": "of the scheduler agent."},
            {"speaker": {"source": "speaker"},
             "text": "Sounds good."},
        ],
        attendees=[], folder_names=[], raw={},
    )
    text = render_note_text(note)
    # The two consecutive 'microphone' turns are merged into one line, so
    # the full spoken phrase is now a literal substring.
    assert "trying to improve the quality of the scheduler agent." in text
    # Different speaker still gets its own line.
    assert "speaker: Sounds good." in text


def test_render_note_text_handles_minimal_note() -> None:
    from mnexa.granola.client import GranolaNote, GranolaUser, render_note_text

    note = GranolaNote(
        note_id="not_xxxxxxxxxxxxxx",
        title="Quick sync",
        created_at="",
        updated_at="",
        owner=GranolaUser(name=None, email=""),
        web_url="",
        summary_text="",
        summary_markdown=None,
        transcript=[],
        attendees=[],
        folder_names=[],
        raw={},
    )
    text = render_note_text(note)
    assert "Quick sync" in text


def test_speaker_label_handles_dict_and_string() -> None:
    from mnexa.granola.client import _speaker_label  # pyright: ignore[reportPrivateUsage]

    assert _speaker_label({"source": "microphone", "diarization_label": "Speaker A"}) == "Speaker A"  # pyright: ignore[reportPrivateUsage]
    assert _speaker_label({"source": "speaker"}) == "speaker"  # pyright: ignore[reportPrivateUsage]
    assert _speaker_label("Alice") == "Alice"  # pyright: ignore[reportPrivateUsage]
    assert _speaker_label(None) == "speaker"  # pyright: ignore[reportPrivateUsage]
