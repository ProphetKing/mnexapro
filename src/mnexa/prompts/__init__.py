"""Stage-1 and Stage-2 prompt templates loaded as package data."""

from __future__ import annotations

from importlib import resources


def load(name: str) -> str:
    return resources.files("mnexa.prompts").joinpath(name).read_text(encoding="utf-8")
