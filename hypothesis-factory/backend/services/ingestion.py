from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

from backend.config import settings
from backend.schemas import KnowledgeBase, SourceDocument
from backend.services.chunking import chunk_document
from backend.services.excel_parser import parse_tailings_excel
from backend.services.pdf_parser import parse_pdf_with_structure


def _stable_id(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


def _parse_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", xml)
    return " ".join(text.split())


def _source_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in {".txt", ".md"}:
        return "txt"
    if ext == ".xlsx":
        return "xlsx"
    if ext in {".png", ".jpg", ".jpeg"}:
        return "image"
    return "unknown"


def parse_source_file(
    path: Path,
    data_dir: Path,
    include_pdf_text: bool = True,
    pdf_max_pages: int | None = None,
) -> tuple[SourceDocument, dict[str, list[object]]]:
    stype = _source_type(path)
    doc_id = _stable_id(path)
    text = ""
    metadata: dict[str, object] = {"relative_path": str(path.relative_to(data_dir))}
    structured: dict[str, list[object]] = {"summaries": [], "size_classes": [], "extractability": []}

    if stype == "docx":
        text = _parse_docx(path)
    elif stype == "txt":
        text = path.read_text(encoding="utf-8", errors="ignore")
    elif stype == "pdf" and include_pdf_text:
        text, pdf_meta, pdf_structured = parse_pdf_with_structure(path, max_pages=pdf_max_pages)
        metadata.update(pdf_meta)
        for key, records in pdf_structured.items():
            structured.setdefault(key, []).extend(records)
    elif stype == "xlsx":
        summaries, size_classes, extractability = parse_tailings_excel(path)
        structured["summaries"].extend(summaries)
        structured["size_classes"].extend(size_classes)
        structured["extractability"].extend(extractability)
        text = _xlsx_as_text(summaries, size_classes, extractability)
    elif stype == "image":
        text = f"Image source: {path.name}. OCR not executed in local mock mode."
        metadata["ocr_required"] = True

    doc = SourceDocument(id=doc_id, path=str(path), source_type=stype, title=path.name, text=text, metadata=metadata)
    return doc, structured


def ingest_path(data_dir: Path, include_pdf_text: bool = True, pdf_max_pages: int | None = None) -> KnowledgeBase:
    kb = KnowledgeBase()
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        doc, structured = parse_source_file(path, data_dir, include_pdf_text=include_pdf_text, pdf_max_pages=pdf_max_pages)
        kb.summaries.extend(structured["summaries"])
        kb.size_classes.extend(structured["size_classes"])
        kb.extractability.extend(structured["extractability"])
        kb.source_documents.append(doc)
        kb.chunks.extend(chunk_document(doc, settings.chunk_size_chars, settings.chunk_overlap_chars))
    return kb


def _xlsx_as_text(summaries, size_classes, extractability) -> str:
    parts: list[str] = []
    for item in summaries:
        parts.append(
            f"{item.plant} {item.stream}: Э28 {item.element28_tonnes} т, "
            f"Э29 {item.element29_tonnes} т, СМТ {item.dry_metric_tonnes}."
        )
    for item in size_classes:
        parts.append(
            f"{item.plant} {item.stream} класс {item.size_class}: "
            f"Э28 {item.element28_tonnes} т, Э29 {item.element29_tonnes} т."
        )
    for item in extractability:
        marker = "извлекаемый" if item.extractable else "неизвлекаемый"
        parts.append(
            f"{item.plant} {item.stream} {marker}: Э28 {item.element28_tonnes} т, "
            f"Э29 {item.element29_tonnes} т."
        )
    return "\n".join(parts)
