from __future__ import annotations

from pathlib import Path


def read_docx(path: Path) -> str:
    try:
        import docx
    except ImportError as e:
        raise RuntimeError(
            "python-docx not installed. Install with: pip install 'mnexa[docx]'"
        ) from e
    doc = docx.Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)
