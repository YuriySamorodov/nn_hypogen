from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from backend.config import settings
from backend.schemas import SourceDocument
from backend.services.corpus_db import stable_hash


DEFAULT_OQMD_FIELDS = [
    "name",
    "entry_id",
    "calculation_id",
    "icsd_id",
    "formationenergy_id",
    "duplicate_entry_id",
    "composition",
    "composition_generic",
    "prototype",
    "spacegroup",
    "volume",
    "ntypes",
    "natoms",
    "unit_cell",
    "sites",
    "band_gap",
    "delta_e",
    "stability",
    "fit",
    "calculation_label",
]


def fetch_oqmd_page(
    *,
    limit: int = 1000,
    offset: int = 0,
    fields: list[str] | None = None,
    filter_value: str | None = None,
    base_url: str | None = None,
    timeout: int = 90,
) -> dict[str, Any]:
    query_fields = fields or DEFAULT_OQMD_FIELDS
    params: dict[str, str] = {
        "limit": str(limit),
        "offset": str(offset),
        "fields": ",".join(query_fields),
    }
    if filter_value:
        params["filter"] = filter_value
    url = f"{(base_url or settings.oqmd_base_url).rstrip('/')}/formationenergy?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "hypothesis-factory OQMD exporter/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OQMD request failed at offset={offset}: {exc}") from exc


def parse_oqmd_fields(value: str | None) -> list[str]:
    if not value:
        return DEFAULT_OQMD_FIELDS
    fields = [item.strip() for item in value.split(",") if item.strip()]
    return fields or DEFAULT_OQMD_FIELDS


def oqmd_source_key(payload: dict[str, Any]) -> str:
    for key in ["formationenergy_id", "entry_id", "calculation_id"]:
        value = payload.get(key)
        if value is not None and str(value) != "":
            if key == "formationenergy_id":
                return str(value)
            break
    entry_id = payload.get("entry_id")
    calculation_id = payload.get("calculation_id")
    if entry_id is not None or calculation_id is not None:
        return f"{entry_id or 'unknown'}:{calculation_id or 'unknown'}"
    return stable_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def build_oqmd_document(payload: dict[str, Any]) -> SourceDocument:
    source_key = oqmd_source_key(payload)
    name = str(payload.get("name") or payload.get("composition") or source_key)
    composition = str(payload.get("composition") or name)
    title = f"OQMD {source_key}: {composition}"
    return SourceDocument(
        id=stable_hash(f"oqmd:{source_key}"),
        path=f"oqmd://formationenergy/{source_key}",
        source_type="oqmd",
        title=title,
        text=_oqmd_text(payload, source_key),
        metadata={
            "provider": "OQMD",
            "license": "CC BY 4.0",
            "api": "OQMD REST formationenergy",
            "source_key": source_key,
            "formationenergy_id": payload.get("formationenergy_id"),
            "entry_id": payload.get("entry_id"),
            "calculation_id": payload.get("calculation_id"),
            "composition": composition,
        },
    )


def _oqmd_text(payload: dict[str, Any], source_key: str) -> str:
    lines = [
        f"OQMD formationenergy_id: {payload.get('formationenergy_id', source_key)}",
        f"Name: {payload.get('name', 'unknown')}",
        f"Composition: {payload.get('composition', 'unknown')}",
        "Source: OQMD REST formationenergy endpoint. Data license: CC BY 4.0.",
    ]
    for key in [
        "composition_generic",
        "prototype",
        "spacegroup",
        "volume",
        "ntypes",
        "natoms",
        "band_gap",
        "delta_e",
        "stability",
        "fit",
        "calculation_label",
    ]:
        if payload.get(key) is not None:
            lines.append(f"{key}: {payload[key]}")
    if payload.get("unit_cell") is not None:
        lines.append("unit_cell: included as normalized JSON payload in structured_records.")
    if payload.get("sites") is not None:
        lines.append("sites: included as normalized JSON payload in structured_records.")
    lines.append("")
    lines.append("Raw OQMD formationenergy JSON:")
    lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)[:12000])
    return "\n".join(lines)
