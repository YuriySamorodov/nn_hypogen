from __future__ import annotations

from typing import Any
from pathlib import Path

from backend.services.pdf_converter import convert_pdf


def parse_pdf(path: Path, max_pages: int | None = None) -> tuple[str, dict[str, object]]:
    """Parse PDF text through the local PDF converter.

    OCR-heavy scanned files may return little text; callers should surface that
    as an access issue rather than silently treating the PDF as empty evidence.
    """
    result = convert_pdf(path, max_pages=max_pages)
    return result.text, result.metadata


def parse_pdf_with_structure(path: Path, max_pages: int | None = None) -> tuple[str, dict[str, object], dict[str, list[dict[str, Any]]]]:
    result = convert_pdf(path, max_pages=max_pages)
    return result.text, result.metadata, result.structured
