from __future__ import annotations

from pathlib import Path


def read_pdf(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError(
            "pdfplumber not installed. Install with: pip install 'mnexa[pdf]'"
        ) from e
    with pdfplumber.open(path) as pdf:
        return "\n\n".join((page.extract_text() or "") for page in pdf.pages)
