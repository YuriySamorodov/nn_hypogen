from __future__ import annotations

import re
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def request_grobid_tei(path: Path, grobid_url: str, timeout: int = 120) -> str:
    """Call GROBID processFulltextDocument and return TEI XML."""

    boundary = f"----hypothesisfactory{uuid.uuid4().hex}"
    body = _multipart_body(boundary, "input", path.name, path.read_bytes(), "application/pdf")
    request = urllib.request.Request(
        f"{grobid_url.rstrip('/')}/api/processFulltextDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="replace")
    if "<TEI" not in payload and "<tei" not in payload:
        raise RuntimeError("GROBID did not return TEI XML")
    return payload


def parse_grobid_tei(tei_xml: str, source_file_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Extract document sections/assets from GROBID TEI.

    The parser intentionally keeps a conservative shape: sections and assets are
    evidence containers, not a full TEI mirror. Raw TEI is stored separately as
    an artifact by the KG worker.
    """

    root = ET.fromstring(tei_xml)
    title = _first_text(root, ".//{*}titleStmt/{*}title") or _first_text(root, ".//{*}title")
    doi = None
    for node in root.findall(".//{*}idno"):
        if (node.attrib.get("type") or "").lower() == "doi":
            doi = _text(node)
            break
    authors = []
    for author in root.findall(".//{*}sourceDesc//{*}author"):
        name = _text(author)
        if name:
            authors.append(name)

    sections: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    idx = 0
    abstract = _first_text(root, ".//{*}profileDesc/{*}abstract")
    if title:
        sections.append(_section(source_file_id, idx, "title", "Title", title, "grobid_tei"))
        idx += 1
    if abstract:
        sections.append(_section(source_file_id, idx, "abstract", "Abstract", abstract, "grobid_tei"))
        idx += 1

    for div in root.findall(".//{*}text/{*}body/{*}div"):
        head = _first_text(div, "./{*}head") or f"Section {idx}"
        paragraphs = [_text(p) for p in div.findall(".//{*}p")]
        text = "\n\n".join(item for item in paragraphs if item)
        if text:
            sections.append(_section(source_file_id, idx, "body", head, text, "grobid_tei"))
            idx += 1

    for ref_idx, ref in enumerate(root.findall(".//{*}text/{*}back//{*}biblStruct")):
        ref_text = _text(ref)
        if ref_text:
            sections.append(_section(source_file_id, idx, "reference", f"Reference {ref_idx + 1}", ref_text, "grobid_tei"))
            idx += 1

    for asset_idx, fig in enumerate(root.findall(".//{*}figure")):
        fig_type = (fig.attrib.get("type") or "").lower()
        asset_type = "table" if fig_type == "table" else "figure"
        caption = _first_text(fig, ".//{*}figDesc") or _first_text(fig, ".//{*}head") or ""
        content = _text(fig)
        if caption or content:
            assets.append(
                {
                    "asset_index": len(assets),
                    "asset_type": asset_type,
                    "label": fig.attrib.get("{http://www.w3.org/XML/1998/namespace}id") or f"{asset_type}-{asset_idx + 1}",
                    "caption": caption,
                    "content": content,
                    "provenance": {"source_file_id": source_file_id, "extractor": "grobid_tei"},
                    "metadata": {"tei_type": fig_type or asset_type},
                }
            )

    for formula_idx, formula in enumerate(root.findall(".//{*}formula")):
        content = _text(formula)
        if content:
            assets.append(
                {
                    "asset_index": len(assets),
                    "asset_type": "formula",
                    "label": formula.attrib.get("{http://www.w3.org/XML/1998/namespace}id") or f"formula-{formula_idx + 1}",
                    "caption": "",
                    "content": content,
                    "provenance": {"source_file_id": source_file_id, "extractor": "grobid_tei"},
                    "metadata": {},
                }
            )

    metadata = {"title": title, "doi": doi, "authors": authors, "extractor": "grobid_tei"}
    return sections, assets, metadata


def fallback_sections_from_text(source_file_id: str, title: str, text: str, extractor: str = "text_fallback") -> list[dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return []
    parts = _split_text_sections(text)
    if len(parts) == 1:
        return [_section(source_file_id, 0, "full_text", title or "Full text", parts[0][1], extractor)]
    return [_section(source_file_id, idx, "body", heading, body, extractor) for idx, (heading, body) in enumerate(parts)]


def assets_from_structured_records(source_file_id: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for row in records:
        record_type = str(row.get("record_type") or "")
        payload = row.get("payload") or {}
        if "formula" in record_type:
            assets.append(
                {
                    "asset_index": len(assets),
                    "asset_type": "formula",
                    "label": f"{record_type}:{payload.get('page', '')}:{payload.get('line', '')}",
                    "caption": "",
                    "content": str(payload.get("text") or payload.get("normalized") or payload),
                    "page": payload.get("page"),
                    "provenance": {"source_file_id": source_file_id, "record_type": record_type},
                    "metadata": payload,
                }
            )
        elif "table" in record_type:
            rows = payload.get("rows") or []
            content = "\n".join("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows[:80])
            assets.append(
                {
                    "asset_index": len(assets),
                    "asset_type": "table",
                    "label": f"{record_type}:{payload.get('page', '')}:{payload.get('start_line', '')}",
                    "caption": "",
                    "content": content or str(payload),
                    "page": payload.get("page"),
                    "provenance": {"source_file_id": source_file_id, "record_type": record_type},
                    "metadata": payload,
                }
            )
    return assets


def _multipart_body(boundary: str, field: str, filename: str, content: bytes, content_type: str) -> bytes:
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return prefix + content + suffix


def _section(source_file_id: str, idx: int, section_type: str, title: str, text: str, extractor: str) -> dict[str, Any]:
    return {
        "section_index": idx,
        "section_type": section_type,
        "title": title,
        "text": text.strip(),
        "provenance": {"source_file_id": source_file_id, "extractor": extractor},
        "metadata": {},
    }


def _split_text_sections(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title = "Full text"
    current: list[str] = []
    heading_re = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?([A-ZА-Я][A-Za-zА-Яа-я0-9 ,:/()\\-]{2,90})\s*$")
    for line in lines:
        clean = line.strip()
        if clean and heading_re.match(clean) and len(current) > 8:
            sections.append((current_title, current))
            current_title = clean
            current = []
        else:
            current.append(line)
    if current:
        sections.append((current_title, current))
    return [(title, "\n".join(body).strip()) for title, body in sections if "\n".join(body).strip()]


def _first_text(root: ET.Element, path: str) -> str:
    node = root.find(path)
    return _text(node) if node is not None else ""


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join(" ".join(node.itertext()).split())
