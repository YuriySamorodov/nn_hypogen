from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PDFOCRResult:
    text: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


_EASYOCR_READER: Any = None
_EASYOCR_LANG_KEY: str | None = None


def _tesseract_langs_to_easyocr(languages: str) -> list[str]:
    """Map Tesseract lang codes (rus+eng) to EasyOCR codes (ru, en)."""

    mapping = {"rus": "ru", "eng": "en", "ru": "ru", "en": "en"}
    codes: list[str] = []
    for raw in re.split(r"[+,\s]+", languages or ""):
        code = mapping.get(raw.strip().lower())
        if code and code not in codes:
            codes.append(code)
    return codes or ["en"]


def _get_easyocr_reader(languages: list[str], gpu: bool) -> Any:
    """Lazily build and cache a single EasyOCR reader per language set.

    Building a reader loads detection/recognition models onto the GPU, which is
    expensive; the worker processes many PDFs sequentially so the reader is
    reused across jobs.
    """

    global _EASYOCR_READER, _EASYOCR_LANG_KEY
    key = f"{'+'.join(languages)}:{gpu}"
    if _EASYOCR_READER is not None and _EASYOCR_LANG_KEY == key:
        return _EASYOCR_READER
    import easyocr  # noqa: F401  (import errors bubble up to caller for fallback)

    _EASYOCR_READER = easyocr.Reader(languages, gpu=gpu)
    _EASYOCR_LANG_KEY = key
    return _EASYOCR_READER


def ocr_pdf_with_easyocr(
    path: Path,
    *,
    max_pages: int,
    dpi: int,
    languages: str,
    gpu: bool = True,
) -> PDFOCRResult:
    """OCR a scanned PDF with EasyOCR on the GPU.

    pypdfium2 renders each page to an image (numpy array); EasyOCR runs
    detection + recognition on the GPU. Falls back to a "skipped" result when
    dependencies are missing so the caller can route to Tesseract instead.
    """

    try:
        import numpy as np
        import pypdfium2 as pdfium
    except ImportError as exc:
        return PDFOCRResult("", "skipped", {"reason": "easyocr_deps_missing", "error": str(exc)})

    lang_codes = _tesseract_langs_to_easyocr(languages)
    try:
        reader = _get_easyocr_reader(lang_codes, gpu)
    except ImportError as exc:
        return PDFOCRResult("", "skipped", {"reason": "easyocr_not_installed", "error": str(exc)})
    except Exception as exc:
        return PDFOCRResult("", "skipped", {"reason": "easyocr_reader_init_failed", "error": str(exc)})

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:
        return PDFOCRResult("", "failed", {"reason": "pdf_render_open_failed", "error": str(exc)})

    pages_total = len(pdf)
    pages_attempted = min(max_pages, pages_total) if max_pages > 0 else pages_total
    page_texts: list[str] = []
    errors: list[dict[str, Any]] = []
    scale = dpi / 72

    for index in range(pages_attempted):
        try:
            page = pdf[index]
            bitmap = page.render(scale=scale)
            image = np.asarray(bitmap.to_pil().convert("RGB"))
        except Exception as exc:
            errors.append({"page": index + 1, "stage": "render", "error": str(exc)})
            continue
        try:
            lines = reader.readtext(image, detail=0, paragraph=True)
        except Exception as exc:
            errors.append({"page": index + 1, "stage": "easyocr", "error": str(exc)})
            continue
        text = "\n".join(str(line).strip() for line in lines if str(line).strip())
        if text:
            page_texts.append(f"\n\n[OCR page {index + 1}]\n{text}")

    text = "\n".join(page_texts).strip()
    quality_score = estimate_ocr_quality(text, max(1, pages_attempted))
    status = "completed" if text else "failed"
    return PDFOCRResult(
        text=text,
        status=status,
        metadata={
            "ocr_engine": "pypdfium2+easyocr-gpu" if gpu else "pypdfium2+easyocr-cpu",
            "pages_total": pages_total,
            "pages_attempted": pages_attempted,
            "ocr_chars": len(text),
            "dpi": dpi,
            "languages": "+".join(lang_codes),
            "quality_score": quality_score,
            "errors": errors[:20],
            "truncated_pages": pages_total > pages_attempted,
            "gpu": gpu,
        },
    )


def ocr_pdf_with_tesseract(
    path: Path,
    *,
    max_pages: int,
    dpi: int,
    languages: str,
    timeout_per_page: int = 120,
) -> PDFOCRResult:
    """OCR a scanned PDF locally.

    pypdfium2 renders PDF pages directly from Python; Tesseract performs OCR on
    the rendered page images. The page cap is intentional because scanned PDFs
    can be hundreds of MB and should stay resumable inside the worker.
    """

    tesseract = shutil.which("tesseract")
    if not tesseract:
        return PDFOCRResult("", "skipped", {"reason": "tesseract_not_found"})

    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        return PDFOCRResult("", "skipped", {"reason": "pypdfium2_not_installed", "error": str(exc)})

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:
        return PDFOCRResult("", "failed", {"reason": "pdf_render_open_failed", "error": str(exc)})

    pages_total = len(pdf)
    pages_attempted = min(max_pages, pages_total) if max_pages > 0 else pages_total
    page_texts: list[str] = []
    errors: list[dict[str, Any]] = []
    scale = dpi / 72

    with tempfile.TemporaryDirectory(prefix="hf_pdf_ocr_") as tmp:
        tmp_dir = Path(tmp)
        for index in range(pages_attempted):
            image_path = tmp_dir / f"page-{index + 1}.png"
            try:
                page = pdf[index]
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                image.save(image_path)
            except Exception as exc:
                errors.append({"page": index + 1, "stage": "render", "error": str(exc)})
                continue

            completed = subprocess.run(
                [tesseract, str(image_path), "stdout", "-l", languages, "--psm", "6"],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_per_page,
            )
            if completed.returncode != 0:
                errors.append(
                    {
                        "page": index + 1,
                        "stage": "tesseract",
                        "returncode": completed.returncode,
                        "stderr": completed.stderr[:1000],
                    }
                )
                continue
            text = completed.stdout.strip()
            if text:
                page_texts.append(f"\n\n[OCR page {index + 1}]\n{text}")

    text = "\n".join(page_texts).strip()
    quality_score = estimate_ocr_quality(text, max(1, pages_attempted))
    status = "completed" if text else "failed"
    return PDFOCRResult(
        text=text,
        status=status,
        metadata={
            "ocr_engine": "pypdfium2+tesseract",
            "pages_total": pages_total,
            "pages_attempted": pages_attempted,
            "ocr_chars": len(text),
            "dpi": dpi,
            "languages": languages,
            "quality_score": quality_score,
            "errors": errors[:20],
            "truncated_pages": pages_total > pages_attempted,
        },
    )


def estimate_ocr_quality(text: str, pages_attempted: int) -> float:
    """Return a coarse 0..1 signal for routing, not a scientific OCR metric."""

    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0.0
    expected_chars = max(900, pages_attempted * 900)
    density = min(len(compact) / expected_chars, 1.0)
    informative = sum(1 for ch in compact if ch.isalnum() or ch in "=+-*/%.,;:()[]{}<>")
    char_quality = informative / max(1, len(compact))
    line_count = max(1, len([line for line in text.splitlines() if line.strip()]))
    avg_line_len = min(len(compact) / line_count / 45, 1.0)
    score = 0.50 * density + 0.35 * char_quality + 0.15 * avg_line_len
    return round(max(0.0, min(score, 1.0)), 3)


def needs_deepseek_ocr_assist(
    result: PDFOCRResult,
    *,
    min_chars: int,
    quality_threshold: float,
) -> bool:
    if result.status != "completed":
        return True
    if len((result.text or "").strip()) < min_chars:
        return True
    return float(result.metadata.get("quality_score") or 0.0) < quality_threshold
