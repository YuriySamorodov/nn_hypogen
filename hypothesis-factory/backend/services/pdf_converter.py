from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MATH_LINE_RE = re.compile(
    r"(?=.*(?:=|≈|≤|≥|->|→|<-|←|\\Delta|\\sum|\\int|\bexp\b|\blog\b|\bsin\b|\bcos\b))"
    r"(?=.*[A-Za-zΑ-Ωα-ω])"
)
CHEMICAL_RE = re.compile(r"\b(?:[A-Z][a-z]?\d*){2,}(?:[+-])?\b")
COLUMN_SPLIT_RE = re.compile(r"\s{2,}")


@dataclass
class PDFConversionResult:
    text: str
    metadata: dict[str, Any]
    structured: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def convert_pdf(path: Path, max_pages: int | None = None) -> PDFConversionResult:
    """Convert a PDF into text plus table/formula records.

    The default implementation stays local-first and uses Poppler's pdftotext,
    which is already available in the Docker image. It extracts:
    - layout-preserving text for retrieval/chunking;
    - heuristic markdown-like tables from aligned text columns;
    - equations and chemical formula candidates as structured records.
    """

    text, metadata = _extract_layout_text(path, max_pages)
    formulas = extract_formula_records(text)
    tables = extract_table_records(text)
    enriched = _compose_enriched_text(text, formulas, tables)
    metadata.update(
        {
            "converter": "poppler_pdftotext_layout",
            "formula_records": len(formulas),
            "table_records": len(tables),
            "ocr_required": len(text.strip()) < 200,
        }
    )
    return PDFConversionResult(
        text=enriched,
        metadata=metadata,
        structured={"pdf_formulas": formulas, "pdf_tables": tables},
    )


def extract_formula_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for page_number, page_text in enumerate(_pages(text), 1):
        for line_number, line in enumerate(page_text.splitlines(), 1):
            clean = _clean_line(line)
            if not clean or len(clean) < 3 or len(clean) > 280:
                continue
            kind = _formula_kind(clean)
            if not kind:
                continue
            key = (page_number, clean)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "page": page_number,
                    "line": line_number,
                    "kind": kind,
                    "text": clean,
                    "normalized": _normalize_formula_text(clean),
                    "confidence": _formula_confidence(clean, kind),
                }
            )
    return records


def extract_table_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page_number, page_text in enumerate(_pages(text), 1):
        current: list[tuple[int, list[str], str]] = []
        for line_number, line in enumerate(page_text.splitlines(), 1):
            cells = _split_table_cells(line)
            if len(cells) >= 2:
                current.append((line_number, cells, line.rstrip()))
                continue
            _flush_table(records, page_number, current)
            current = []
        _flush_table(records, page_number, current)
    return records


def _extract_layout_text(path: Path, max_pages: int | None) -> tuple[str, dict[str, Any]]:
    cmd = ["pdftotext", "-layout"]
    if max_pages:
        cmd.extend(["-f", "1", "-l", str(max_pages)])
    cmd.extend([str(path), "-"])
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=90)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return "", {"parser": "pdftotext", "error": str(exc), "bytes": path.stat().st_size}
    metadata: dict[str, Any] = {
        "parser": "pdftotext",
        "returncode": completed.returncode,
        "bytes": path.stat().st_size,
        "pages_detected": max(1, len(_pages(completed.stdout or ""))) if completed.stdout else 0,
    }
    if completed.stderr:
        metadata["stderr"] = completed.stderr[:500]
    return completed.stdout or "", metadata


def _compose_enriched_text(text: str, formulas: list[dict[str, Any]], tables: list[dict[str, Any]]) -> str:
    sections = [text.strip()]
    if formulas:
        formula_lines = ["", "Extracted PDF formulas and equations:"]
        for item in formulas:
            formula_lines.append(f"[p.{item['page']}:{item['line']}] {item['kind']}: {item['text']}")
        sections.append("\n".join(formula_lines))
    if tables:
        table_lines = ["", "Extracted PDF tables:"]
        for idx, table in enumerate(tables, 1):
            table_lines.append(f"Table {idx} p.{table['page']} rows={table['row_count']} cols={table['column_count']}")
            for row in table["rows"][:20]:
                table_lines.append("| " + " | ".join(row) + " |")
        sections.append("\n".join(table_lines))
    return "\n\n".join(section for section in sections if section.strip())


def _pages(text: str) -> list[str]:
    return text.split("\f") if text else []


def _clean_line(line: str) -> str:
    return " ".join(line.strip().split())


def _formula_kind(line: str) -> str | None:
    if CHEMICAL_RE.search(line) and _looks_like_chemical_context(line):
        return "chemical_formula"
    if MATH_LINE_RE.search(line) and _looks_like_equation(line):
        return "equation"
    return None


def _looks_like_chemical_context(line: str) -> bool:
    return any(token in line for token in ["+", "-", "·", "(", ")", "[", "]"]) or len(line.split()) <= 8


def _looks_like_equation(line: str) -> bool:
    compact = line.replace(" ", "")
    if len(compact) < 5:
        return False
    math_chars = sum(1 for ch in compact if ch in "=+-*/^_()[]{}<>≈≤≥→←")
    digit_or_var = sum(1 for ch in compact if ch.isdigit() or ch.isalpha())
    return math_chars >= 1 and digit_or_var >= 3


def _formula_confidence(line: str, kind: str) -> float:
    score = 0.55
    if "=" in line:
        score += 0.2
    if any(op in line for op in ["≈", "≤", "≥", "→", "<-", "->"]):
        score += 0.1
    if kind == "chemical_formula" and CHEMICAL_RE.search(line):
        score += 0.15
    return min(score, 0.95)


def _normalize_formula_text(line: str) -> str:
    normalized = line.replace("−", "-").replace("–", "-").replace("—", "-")
    normalized = normalized.replace("×", "*").replace("·", ".")
    return " ".join(normalized.split())


def _split_table_cells(line: str) -> list[str]:
    stripped = line.rstrip()
    if not stripped.strip():
        return []
    cells = [cell.strip() for cell in COLUMN_SPLIT_RE.split(stripped.strip()) if cell.strip()]
    if len(cells) < 2:
        return []
    numeric_cells = sum(1 for cell in cells if re.search(r"\d", cell))
    if numeric_cells == 0 and len(cells) < 3:
        return []
    return cells


def _flush_table(records: list[dict[str, Any]], page_number: int, rows: list[tuple[int, list[str], str]]) -> None:
    if len(rows) < 2:
        return
    max_cols = max(len(cells) for _, cells, _ in rows)
    if max_cols < 2:
        return
    normalized_rows = [cells + [""] * (max_cols - len(cells)) for _, cells, _ in rows]
    records.append(
        {
            "page": page_number,
            "start_line": rows[0][0],
            "end_line": rows[-1][0],
            "row_count": len(rows),
            "column_count": max_cols,
            "rows": normalized_rows,
            "raw_lines": [raw for _, _, raw in rows],
            "confidence": 0.75 if len(rows) >= 3 else 0.6,
        }
    )
