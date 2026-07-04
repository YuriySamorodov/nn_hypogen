from __future__ import annotations

import json
from typing import Any, Iterable

from backend.config import settings
from backend.schemas import SourceDocument
from backend.services.corpus_db import stable_hash


DEFAULT_MP_FIELDS = [
    "material_id",
    "formula_pretty",
    "band_gap",
    "energy_above_hull",
    "formation_energy_per_atom",
    "symmetry",
    "structure",
]


def fetch_materials_project_summaries(
    *,
    api_key: str | None = None,
    chemsys: str | None = None,
    elements: list[str] | None = None,
    fields: list[str] | None = None,
    limit: int | None = None,
    allow_all: bool = False,
) -> list[dict[str, Any]]:
    """Fetch Materials Project summary docs through the official mp-api client."""

    query_fields = fields or DEFAULT_MP_FIELDS
    docs = _materials_project_search(api_key=api_key, chemsys=chemsys, elements=elements, fields=query_fields, allow_all=allow_all)

    payloads = [_mp_doc_to_payload(doc, query_fields) for doc in docs]
    return payloads[:limit] if limit else payloads


def fetch_materials_project_page(
    *,
    api_key: str | None = None,
    chemsys: str | None = None,
    elements: list[str] | None = None,
    fields: list[str] | None = None,
    chunk_size: int = 1000,
    page: int = 1,
    allow_all: bool = False,
) -> list[dict[str, Any]]:
    query_fields = fields or DEFAULT_MP_FIELDS
    docs = _materials_project_search(
        api_key=api_key,
        chemsys=chemsys,
        elements=elements,
        fields=query_fields,
        allow_all=allow_all,
        chunk_size=chunk_size,
        page=page,
    )
    return [_mp_doc_to_payload(doc, query_fields) for doc in docs]


def build_materials_project_document(payload: dict[str, Any]) -> SourceDocument:
    material_id = str(payload.get("material_id") or payload.get("material_id.string") or "unknown")
    formula = str(payload.get("formula_pretty") or payload.get("formula") or material_id)
    title = f"Materials Project {material_id}: {formula}"
    text = _materials_project_text(payload)
    return SourceDocument(
        id=stable_hash(f"materials_project:{material_id}"),
        path=f"materials_project://{material_id}",
        source_type="materials_project",
        title=title,
        text=text,
        metadata={
            "provider": "Materials Project",
            "license": "CC BY 4.0",
            "material_id": material_id,
            "formula_pretty": formula,
            "api": "mp-api",
        },
    )


def build_materials_project_records(payloads: Iterable[dict[str, Any]]) -> list[tuple[SourceDocument, dict[str, Any]]]:
    return [(build_materials_project_document(payload), payload) for payload in payloads]


def parse_fields(value: str | None) -> list[str]:
    if not value:
        return DEFAULT_MP_FIELDS
    fields = [item.strip() for item in value.split(",") if item.strip()]
    return fields or DEFAULT_MP_FIELDS


def parse_elements(value: str | None) -> list[str] | None:
    if not value:
        return None
    elements = [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    return elements or None


def _materials_project_search(
    *,
    api_key: str | None,
    chemsys: str | None,
    elements: list[str] | None,
    fields: list[str],
    allow_all: bool,
    chunk_size: int | None = None,
    page: int | None = None,
) -> list[Any]:
    key = api_key or settings.materials_project_api_key
    if not key:
        raise RuntimeError("MP_API_KEY or MATERIALS_PROJECT_API_KEY is not set")
    if not chemsys and not elements and not allow_all:
        raise RuntimeError("Set --chemsys/--elements or pass --all for Materials Project ingestion")

    try:
        from mp_api.client import MPRester
    except Exception as exc:
        raise RuntimeError("Install a compatible mp-api environment to use Materials Project ingestion") from exc

    kwargs: dict[str, Any] = {"fields": fields, "all_fields": False}
    if chemsys:
        kwargs["chemsys"] = chemsys
    if elements:
        kwargs["elements"] = elements
    if chunk_size:
        kwargs["chunk_size"] = chunk_size
        kwargs["num_chunks"] = 1
    if page:
        kwargs["_page"] = page

    with MPRester(api_key=key, endpoint=settings.materials_project_base_url) as mpr:
        return list(mpr.materials.summary.search(**kwargs))


def _mp_doc_to_payload(doc: Any, fields: list[str]) -> dict[str, Any]:
    if hasattr(doc, "model_dump"):
        raw = doc.model_dump()
    elif hasattr(doc, "dict"):
        raw = doc.dict()
    else:
        raw = {field: getattr(doc, field, None) for field in fields}
    return {field: _to_jsonable(raw.get(field, getattr(doc, field, None))) for field in fields if raw.get(field, getattr(doc, field, None)) is not None}


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return _to_jsonable(value.as_dict())
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump())
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _materials_project_text(payload: dict[str, Any]) -> str:
    material_id = payload.get("material_id", "unknown")
    formula = payload.get("formula_pretty", "unknown")
    symmetry = payload.get("symmetry") or {}
    symmetry_symbol = symmetry.get("symbol") if isinstance(symmetry, dict) else symmetry
    lines = [
        f"Materials Project material_id: {material_id}",
        f"Formula: {formula}",
        "Source: Materials Project mp-api summary endpoint. Data license: CC BY 4.0.",
    ]
    for key in ["energy_above_hull", "formation_energy_per_atom", "band_gap"]:
        if key in payload:
            lines.append(f"{key}: {payload[key]}")
    if symmetry_symbol:
        lines.append(f"symmetry: {symmetry_symbol}")
    if "structure" in payload:
        lines.append("structure: included as normalized JSON payload in structured_records.")
    lines.append("")
    lines.append("Raw summary JSON:")
    lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)[:12000])
    return "\n".join(lines)
