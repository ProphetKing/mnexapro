"""Source-document parsers. Dispatch by file extension; uses markitdown for complex formats."""

from __future__ import annotations

from pathlib import Path


def read_source(path: Path) -> str:
    """Extract plain text/markdown from a raw source document.
    
    Uses markitdown for binary formats (pdf, docx, pptx, xlsx, images) 
    to get better structured markdown output.
    """
    suffix = path.suffix.lower()
    
    # 1. 纯文本文件直接读取，速度最快
    if suffix in {".md", ".markdown", ".txt", ""}:
        return path.read_text(encoding="utf-8")
    
    # 2. 使用 markitdown 处理二进制/复杂格式
    # 支持的格式包括: .pdf, .docx, .pptx, .xlsx, .jpg, .png, .wav, .mp3 等
    if suffix in {".pdf", ".docx", ".pptx", ".xlsx", ".jpg", ".jpeg", ".png", ".wav", ".mp3"}:
        try:
            from markitdown import MarkItDown
            md_converter = MarkItDown()
            result = md_converter.convert(str(path))
            # markitdown 返回的对象中，text_content 是转换后的 markdown 字符串
            return result.text_content
        except ImportError:
            raise ImportError(
                "markitdown is required for this file type. "
                "Please install it via: pip install markitdown"
            )
        except Exception as e:
            # 如果 markitdown 失败，记录错误并抛出，避免静默失败
            raise ValueError(f"Failed to parse {path.name} with markitdown: {e}")

    raise ValueError(
        f"unsupported file type: {suffix!r}. "
        "v0 supports .md, .txt, and markitdown-supported formats (.pdf, .docx, etc.)."
    )
# """Source-document parsers. Dispatch by file extension; lazy-import heavies."""

# from __future__ import annotations

# from pathlib import Path


# def read_source(path: Path) -> str:
#     """Extract plain text from a raw source document."""
#     suffix = path.suffix.lower()
#     if suffix in {".md", ".markdown", ".txt", ""}:
#         return path.read_text(encoding="utf-8")
#     if suffix == ".pdf":
#         from mnexa.parsers.pdf import read_pdf

#         return read_pdf(path)
#     if suffix == ".docx":
#         from mnexa.parsers.docx import read_docx

#         return read_docx(path)
#     raise ValueError(
#         f"unsupported file type: {suffix!r}. v0 supports .md, .txt, .pdf, .docx."
#     )
