from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from backend.config import settings
from backend.schemas import Evidence, SourceRef
from backend.services.corpus_db import CorpusStore, _loads, stable_hash
from backend.services.scientific_pdf import (
    assets_from_structured_records,
    fallback_sections_from_text,
    parse_grobid_tei,
    request_grobid_tei,
)


KG_ENTITY_TYPES = {
    "material",
    "material_class",
    "alloy",
    "element",
    "phase",
    "property",
    "method",
    "process",
    "defect",
    "application",
    "term",
}

ELEMENT_SYMBOLS = {
    "H",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
    "Ga",
    "Ge",
    "As",
    "Se",
    "Br",
    "Rb",
    "Sr",
    "Y",
    "Zr",
    "Nb",
    "Mo",
    "Ru",
    "Rh",
    "Pd",
    "Ag",
    "Cd",
    "In",
    "Sn",
    "Sb",
    "Te",
    "I",
    "Cs",
    "Ba",
    "La",
    "Ce",
    "Pr",
    "Nd",
    "Sm",
    "Eu",
    "Gd",
    "Tb",
    "Dy",
    "Ho",
    "Er",
    "Tm",
    "Yb",
    "Lu",
    "Hf",
    "Ta",
    "W",
    "Re",
    "Os",
    "Ir",
    "Pt",
    "Au",
    "Hg",
    "Tl",
    "Pb",
    "Bi",
    "Th",
    "U",
}

TERM_CATALOG: dict[str, list[str]] = {
    "property": [
        "yield strength",
        "tensile strength",
        "ultimate tensile strength",
        "hardness",
        "fatigue",
        "corrosion",
        "oxidation resistance",
        "band gap",
        "formation energy",
        "energy above hull",
        "thermal conductivity",
        "electrical conductivity",
        "density",
        "ductility",
        "fracture toughness",
        "elastic modulus",
        "young's modulus",
        "wear resistance",
        "coercivity",
    ],
    "method": [
        "dft",
        "density functional theory",
        "xrd",
        "sem",
        "tem",
        "ebsd",
        "eds",
        "edx",
        "xps",
        "raman",
        "ftir",
        "dsc",
        "calphad",
        "molecular dynamics",
        "finite element",
        "phase-field",
        "neutron diffraction",
        "synchrotron",
    ],
    "process": [
        "slm",
        "lpbf",
        "selective laser melting",
        "laser powder bed fusion",
        "additive manufacturing",
        "heat treatment",
        "annealing",
        "quenching",
        "tempering",
        "sintering",
        "casting",
        "rolling",
        "extrusion",
        "welding",
        "flotation",
        "hydrometallurgy",
        "pyrometallurgy",
        "electrodeposition",
    ],
    "phase": [
        "austenite",
        "martensite",
        "ferrite",
        "cementite",
        "sigma phase",
        "laves phase",
        "bcc",
        "fcc",
        "hcp",
        "perovskite",
        "spinel",
    ],
    "defect": [
        "porosity",
        "crack",
        "vacancy",
        "dislocation",
        "grain boundary",
        "segregation",
        "inclusion",
        "precipitate",
        "pitting",
    ],
    "application": [
        "biomedical",
        "implant",
        "aerospace",
        "battery",
        "fuel cell",
        "hydrogen storage",
        "electrocatalysis",
        "photocatalysis",
        "thermoelectric",
        "photovoltaic",
        "membrane",
        "carbon capture",
        "nuclear",
        "high temperature",
    ],
    "material_class": [
        "stainless steel",
        "austenitic stainless steel",
        "titanium alloy",
        "aluminum alloy",
        "nickel superalloy",
        "high entropy alloy",
        "ceramic",
        "polymer",
        "composite",
        "oxide",
        "sulfide",
        "perovskite",
        "hydrogel",
        "biomaterial",
    ],
}

ALLOY_RE = re.compile(
    r"\b(316L|304L|17-4PH|Ti-?6Al-?4V|AlSi10Mg|CoCrMo|NiTi|Inconel\s*\d+|Hastelloy\s*[A-Z0-9-]+|Fe-?Cr-?Ni|Al-?Cu-?Mg|Cu-?Ni)\b",
    flags=re.IGNORECASE,
)
FORMULA_RE = re.compile(r"\b(?:[A-Z][a-z]?\d*){2,}(?:[+-])?\b")


@dataclass
class KGBuildResult:
    sections: int = 0
    assets: int = 0
    entities: int = 0
    relations: int = 0
    embeddings: int = 0
    neo4j_status: str = "not_requested"
    qdrant_status: str = "not_requested"


def build_document_layers(store: CorpusStore, run_id: str, grobid: str = "auto") -> dict[str, int]:
    counts = {"sections": 0, "assets": 0, "grobid_completed": 0, "grobid_degraded": 0}
    sources = store.fetchall(
        """
        SELECT sf.*, dt.title, dt.text, dt.metadata AS document_metadata
        FROM source_files sf
        JOIN document_texts dt ON dt.source_file_id=sf.id
        WHERE sf.run_id=?
        ORDER BY sf.relative_path
        """,
        (run_id,),
    )
    for source in sources:
        sections: list[dict[str, Any]] = []
        assets: list[dict[str, Any]] = []
        metadata = _loads(source.get("document_metadata"), {})
        if source["source_type"] == "pdf" and grobid != "off" and settings.grobid_url:
            try:
                tei = request_grobid_tei(Path(source["path"]), settings.grobid_url, timeout=settings.kg_grobid_timeout_seconds)
                sections, assets, tei_metadata = parse_grobid_tei(tei, source["id"])
                store.save_artifact(
                    run_id,
                    kind="grobid_tei",
                    stage="kg_sections",
                    status="completed",
                    source_file_id=source["id"],
                    content=tei[:200000],
                    metadata=tei_metadata,
                )
                counts["grobid_completed"] += 1
            except Exception as exc:
                store.save_artifact(
                    run_id,
                    kind="grobid_tei",
                    stage="kg_sections",
                    status="failed" if grobid == "always" else "skipped",
                    source_file_id=source["id"],
                    metadata={"error": str(exc), "mode": grobid},
                )
                counts["grobid_degraded"] += 1

        if not sections:
            sections = fallback_sections_from_text(source["id"], source["title"], source["text"] or "", metadata.get("parser") or "text_fallback")
        structured_rows = [
            {"record_type": row["record_type"], "payload": _loads(row["payload"], {})}
            for row in store.fetchall(
                "SELECT record_type, payload FROM structured_records WHERE run_id=? AND source_file_id=?",
                (run_id, source["id"]),
            )
        ]
        existing_asset_keys = {(asset.get("asset_type"), asset.get("content")) for asset in assets}
        for asset in assets_from_structured_records(source["id"], structured_rows):
            key = (asset.get("asset_type"), asset.get("content"))
            if key not in existing_asset_keys:
                assets.append(asset)
                existing_asset_keys.add(key)
        store.replace_document_sections(run_id, source["id"], sections)
        store.replace_document_assets(run_id, source["id"], assets)
        counts["sections"] += len(sections)
        counts["assets"] += len(assets)
    return counts


def build_entities(store: CorpusStore, run_id: str) -> list[dict[str, Any]]:
    observations: dict[tuple[str, str], dict[str, Any]] = {}
    contexts = _entity_context_rows(store, run_id)
    for row in contexts:
        text = row["text"] or ""
        source_file_id = row["source_file_id"]
        chunk_id = row.get("chunk_id")
        for entity_type, name, confidence, aliases in _extract_entities_from_text(text):
            key = (entity_type, _normalize(name))
            item = observations.setdefault(
                key,
                {
                    "id": stable_hash(f"{run_id}:{entity_type}:{_normalize(name)}"),
                    "run_id": run_id,
                    "entity_type": entity_type,
                    "name": name,
                    "normalized": _normalize(name),
                    "confidence": confidence,
                    "source_count": 0,
                    "first_source_file_id": source_file_id,
                    "aliases": [],
                    "metadata": {"observations": []},
                },
            )
            item["source_count"] += 1
            item["confidence"] = max(float(item["confidence"]), confidence)
            item["metadata"]["observations"].append({"source_file_id": source_file_id, "chunk_id": chunk_id, "text": _snippet(text, name)})
            item["aliases"] = sorted(set(item.get("aliases", [])) | set(aliases))

    for row in store.fetchall("SELECT source_file_id, record_type, payload FROM structured_records WHERE run_id=?", (run_id,)):
        payload = _loads(row["payload"], {})
        for entity in _entities_from_structured_record(run_id, row["source_file_id"], row["record_type"], payload):
            key = (entity["entity_type"], entity["normalized"])
            existing = observations.get(key)
            if existing:
                existing["source_count"] += 1
                existing["metadata"].setdefault("structured_records", []).append({"record_type": row["record_type"], "source_file_id": row["source_file_id"]})
            else:
                observations[key] = entity

    entities = list(observations.values())
    entities.sort(key=lambda item: (item["entity_type"], item["normalized"]))
    store.replace_kg_entities(run_id, entities)
    return entities


def build_relations(store: CorpusStore, run_id: str, entities: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    entities = entities or store.fetchall("SELECT * FROM kg_entities WHERE run_id=?", (run_id,))
    by_norm = {(row["entity_type"], row["normalized"]): row for row in entities}
    rels: dict[str, dict[str, Any]] = {}
    contexts = _entity_context_rows(store, run_id)
    for row in contexts:
        found = list(_entities_in_text(row["text"] or "", by_norm))
        materials = [item for item in found if item["entity_type"] in {"material", "alloy"}]
        for material in materials:
            for other in found:
                if material["id"] == other["id"]:
                    continue
                predicate = _predicate_for(other["entity_type"])
                if not predicate:
                    continue
                relation = {
                    "subject_entity_id": material["id"],
                    "predicate": predicate,
                    "object_entity_id": other["id"],
                    "evidence_text": _snippet(row["text"] or "", material["name"]),
                    "evidence_chunk_id": row.get("chunk_id"),
                    "source_file_id": row["source_file_id"],
                    "confidence": 0.62,
                    "extractor": "cooccurrence_rules",
                    "provenance": {"source": row.get("context_type", "chunk")},
                    "metadata": {"subject": material["name"], "object": other["name"]},
                }
                relation["id"] = stable_hash(f"{run_id}:{relation['subject_entity_id']}:{predicate}:{relation['object_entity_id']}:{relation.get('evidence_chunk_id') or relation.get('source_file_id')}")
                rels[relation["id"]] = relation

            for class_name in _classes_for_material(material["name"]):
                class_entity = by_norm.get(("material_class", _normalize(class_name)))
                if class_entity:
                    relation = {
                        "subject_entity_id": material["id"],
                        "predicate": "belongs_to_class",
                        "object_entity_id": class_entity["id"],
                        "evidence_text": material["name"],
                        "evidence_chunk_id": row.get("chunk_id"),
                        "source_file_id": row["source_file_id"],
                        "confidence": 0.8,
                        "extractor": "class_rules",
                        "provenance": {"rule": "material_name_classification"},
                        "metadata": {"subject": material["name"], "object": class_name},
                    }
                    relation["id"] = stable_hash(f"{run_id}:{material['id']}:belongs_to_class:{class_entity['id']}:{row.get('source_file_id')}")
                    rels[relation["id"]] = relation

    for row in store.fetchall("SELECT source_file_id, payload FROM structured_records WHERE run_id=? AND record_type='openalex_work'", (run_id,)):
        payload = _loads(row["payload"], {})
        for cited in payload.get("referenced_works") or []:
            rel_id = stable_hash(f"{run_id}:{row['source_file_id']}:cites:{cited}")
            rels[rel_id] = {
                "id": rel_id,
                "predicate": "cites",
                "object_value": str(cited),
                "evidence_text": str(cited),
                "source_file_id": row["source_file_id"],
                "confidence": 0.9,
                "extractor": "openalex_metadata",
                "provenance": {"record_type": "openalex_work"},
                "metadata": {},
            }

    if settings.kg_llm_relations != "off":
        _append_deepseek_relations(store, run_id, by_norm, rels)

    relations = list(rels.values())
    relations.sort(key=lambda item: (item.get("predicate", ""), item.get("subject_entity_id") or "", item.get("object_entity_id") or item.get("object_value") or ""))
    store.replace_kg_relations(run_id, relations)
    return relations


def _append_deepseek_relations(
    store: CorpusStore,
    run_id: str,
    by_norm: dict[tuple[str, str], dict[str, Any]],
    rels: dict[str, dict[str, Any]],
) -> None:
    purpose = "kg_relation_extraction"
    if not settings.deepseek_api_key:
        store.log_llm_call(run_id, "deepseek", settings.deepseek_model_struct, purpose, "skipped", 0, 0, "DEEPSEEK_API_KEY is not set")
        store.save_artifact(
            run_id,
            kind="deepseek_kg_relations",
            stage="kg_relations",
            status="skipped",
            metadata={"reason": "missing_api_key", "mode": settings.kg_llm_relations},
        )
        return

    contexts = _entity_context_rows(store, run_id)[:25]
    entity_lines = [f"{row['id']}\t{row['entity_type']}\t{row['name']}" for row in by_norm.values()]
    prompt = (
        "Extract high-confidence materials-science relations from the text snippets. "
        "Use only entities from the provided entity list. Return valid JSON with key relations, "
        "where each relation has subject, predicate, object, evidence_text, confidence. "
        "Allowed predicates: has_property, studied_by_method, produced_by_process, belongs_to_class, "
        "has_phase, has_defect, mentions_application, similar_to. Do not invent entities.\n\n"
        "ENTITIES:\n"
        f"{chr(10).join(entity_lines[:400])}\n\n"
        "TEXT:\n"
        + "\n\n".join(f"[{idx}] source={row['source_file_id']} chunk={row.get('chunk_id')}\n{row['text'][:1200]}" for idx, row in enumerate(contexts))
    )
    body = json.dumps(
        {
            "model": settings.deepseek_model_struct,
            "messages": [
                {"role": "system", "content": "Return valid JSON only. Preserve evidence and uncertainty."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.deepseek_base_url}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = json.loads(response.read().decode("utf-8"))
        response_text = raw["choices"][0]["message"]["content"]
        payload = json.loads(response_text)
        added = 0
        entity_by_name = {row["name"].lower(): row for row in by_norm.values()}
        entity_by_id = {row["id"]: row for row in by_norm.values()}
        for item in payload.get("relations", []):
            subject = entity_by_id.get(str(item.get("subject"))) or entity_by_name.get(str(item.get("subject", "")).lower())
            obj = entity_by_id.get(str(item.get("object"))) or entity_by_name.get(str(item.get("object", "")).lower())
            predicate = str(item.get("predicate") or "")
            if not subject or not obj or predicate not in {
                "has_property",
                "studied_by_method",
                "produced_by_process",
                "belongs_to_class",
                "has_phase",
                "has_defect",
                "mentions_application",
                "similar_to",
            }:
                continue
            rel_id = stable_hash(f"{run_id}:deepseek:{subject['id']}:{predicate}:{obj['id']}:{item.get('evidence_text', '')[:80]}")
            rels[rel_id] = {
                "id": rel_id,
                "subject_entity_id": subject["id"],
                "predicate": predicate,
                "object_entity_id": obj["id"],
                "evidence_text": str(item.get("evidence_text") or "")[:2000],
                "confidence": float(item.get("confidence", 0.65)),
                "extractor": "deepseek",
                "provenance": {"model": settings.deepseek_model_struct},
                "metadata": {"subject": subject["name"], "object": obj["name"]},
            }
            added += 1
        store.log_llm_call(run_id, "deepseek", settings.deepseek_model_struct, purpose, "completed", len(prompt), len(response_text))
        store.save_artifact(
            run_id,
            kind="deepseek_kg_relations",
            stage="kg_relations",
            status="completed",
            content=response_text[:4000],
            metadata={"relations_added": added, "model": settings.deepseek_model_struct},
        )
    except Exception as exc:
        store.log_llm_call(run_id, "deepseek", settings.deepseek_model_struct, purpose, "failed", len(prompt), 0, str(exc))
        store.save_artifact(
            run_id,
            kind="deepseek_kg_relations",
            stage="kg_relations",
            status="failed",
            metadata={"error": str(exc), "model": settings.deepseek_model_struct},
        )


def build_embeddings(store: CorpusStore, run_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(
        {
            "target_type": "documents",
            "target_id": row["source_file_id"],
            "text": f"{row['title']}\n{row['text']}",
            "payload": {"source_file_id": row["source_file_id"], "title": row["title"], "source_type": row["source_type"]},
        }
        for row in store.fetchall(
            """
            SELECT sf.source_type, dt.source_file_id, dt.title, dt.text
            FROM document_texts dt JOIN source_files sf ON sf.id=dt.source_file_id
            WHERE dt.run_id=?
            """,
            (run_id,),
        )
    )
    rows.extend(
        {
            "target_type": "chunks",
            "target_id": row["id"],
            "text": row["text"],
            "payload": {"source_file_id": row["source_file_id"], "document_id": row["document_id"], "chunk_index": row["chunk_index"]},
        }
        for row in store.fetchall("SELECT id, source_file_id, document_id, chunk_index, text FROM document_chunks WHERE run_id=?", (run_id,))
    )
    rows.extend(
        {
            "target_type": "entities",
            "target_id": row["id"],
            "text": f"{row['entity_type']}: {row['name']} {row.get('description') or ''}",
            "payload": {"entity_type": row["entity_type"], "name": row["name"], "normalized": row["normalized"]},
        }
        for row in store.fetchall("SELECT * FROM kg_entities WHERE run_id=?", (run_id,))
    )
    embeddings = []
    for row in rows:
        vector = embed_text(row["text"], settings.kg_embedding_dimensions)
        embeddings.append(
            {
                "id": stable_hash(f"{run_id}:{row['target_type']}:{row['target_id']}:{settings.kg_embedding_model}"),
                "run_id": run_id,
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "model": settings.kg_embedding_model,
                "dimensions": len(vector),
                "embedding": vector,
                "payload": {**row["payload"], "run_id": run_id, "text_preview": row["text"][:800]},
            }
        )
    store.replace_kg_embeddings(run_id, embeddings)
    return embeddings


def embed_text(text: str, dimensions: int = 384) -> list[float]:
    try:
        from sklearn.feature_extraction.text import HashingVectorizer

        vectorizer = HashingVectorizer(n_features=dimensions, alternate_sign=False, norm="l2", ngram_range=(1, 2))
        vector = vectorizer.transform([text or ""]).toarray()[0]
    except Exception:
        vector = np.zeros(dimensions, dtype=float)
        for token in _terms(text):
            vector[int(stable_hash(token), 16) % dimensions] += 1.0
        norm = np.linalg.norm(vector)
        if norm:
            vector = vector / norm
    return [float(x) for x in vector]


def sync_neo4j(store: CorpusStore, run_id: str) -> dict[str, Any]:
    if not settings.neo4j_uri or not settings.neo4j_password:
        store.upsert_kg_sync_status(run_id, "neo4j", "skipped", error="NEO4J_URI or NEO4J_PASSWORD is not set")
        return {"status": "skipped", "error": "NEO4J_URI or NEO4J_PASSWORD is not set"}
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        store.upsert_kg_sync_status(run_id, "neo4j", "skipped", error=str(exc))
        return {"status": "skipped", "error": str(exc)}
    entities = store.fetchall("SELECT * FROM kg_entities WHERE run_id=?", (run_id,))
    relations = store.fetchall("SELECT * FROM kg_relations WHERE run_id=?", (run_id,))
    documents = store.fetchall(
        """
        SELECT sf.id, sf.source_type, sf.relative_path, dt.title
        FROM source_files sf LEFT JOIN document_texts dt ON dt.source_file_id=sf.id
        WHERE sf.run_id=?
        """,
        (run_id,),
    )
    sections = store.fetchall("SELECT id, source_file_id, section_type, title FROM document_sections WHERE run_id=?", (run_id,))
    chunks = store.fetchall("SELECT id, source_file_id, chunk_index FROM document_chunks WHERE run_id=?", (run_id,))
    try:
        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
        with driver.session() as session:
            session.run("CREATE CONSTRAINT hf_kg_node_id IF NOT EXISTS FOR (n:KGNode) REQUIRE n.id IS UNIQUE")
            for doc in documents:
                session.run(
                    """
                    MERGE (d:KGNode:Document {id: $id})
                    SET d.run_id=$run_id, d.title=$title, d.source_type=$source_type, d.path=$path
                    """,
                    id=doc["id"],
                    run_id=run_id,
                    title=doc.get("title") or "",
                    source_type=doc["source_type"],
                    path=doc["relative_path"],
                )
            for section in sections:
                session.run(
                    """
                    MERGE (s:KGNode:Section {id: $id})
                    SET s.run_id=$run_id, s.title=$title, s.section_type=$section_type
                    WITH s
                    MATCH (d:Document {id: $source_file_id})
                    MERGE (d)-[:HAS_SECTION]->(s)
                    """,
                    id=section["id"],
                    run_id=run_id,
                    title=section["title"],
                    section_type=section["section_type"],
                    source_file_id=section["source_file_id"],
                )
            for chunk in chunks:
                session.run(
                    """
                    MERGE (c:KGNode:Chunk {id: $id})
                    SET c.run_id=$run_id, c.chunk_index=$chunk_index
                    WITH c
                    MATCH (d:Document {id: $source_file_id})
                    MERGE (d)-[:HAS_CHUNK]->(c)
                    """,
                    id=chunk["id"],
                    run_id=run_id,
                    chunk_index=chunk["chunk_index"],
                    source_file_id=chunk["source_file_id"],
                )
            for entity in entities:
                label = _neo4j_label(entity["entity_type"])
                session.run(
                    f"""
                    MERGE (e:KGNode:Entity:`{label}` {{id: $id}})
                    SET e.run_id=$run_id, e.entity_type=$entity_type, e.name=$name,
                        e.normalized=$normalized, e.confidence=$confidence
                    """,
                    id=entity["id"],
                    run_id=run_id,
                    entity_type=entity["entity_type"],
                    name=entity["name"],
                    normalized=entity["normalized"],
                    confidence=entity["confidence"],
                )
            for rel in relations:
                subject = rel.get("subject_entity_id")
                obj = rel.get("object_entity_id")
                if subject and obj:
                    session.run(
                        """
                        MATCH (s:KGNode {id: $subject})
                        MATCH (o:KGNode {id: $object})
                        MERGE (s)-[r:KG_RELATION {id: $id}]->(o)
                        SET r.predicate=$predicate, r.confidence=$confidence,
                            r.evidence_text=$evidence_text, r.source_file_id=$source_file_id
                        """,
                        id=rel["id"],
                        subject=subject,
                        object=obj,
                        predicate=rel["predicate"],
                        confidence=rel["confidence"],
                        evidence_text=rel["evidence_text"][:2000],
                        source_file_id=rel.get("source_file_id"),
                    )
        driver.close()
        counts = {"documents": len(documents), "sections": len(sections), "chunks": len(chunks), "entities": len(entities), "relations": len(relations)}
        store.upsert_kg_sync_status(run_id, "neo4j", "completed", counts=counts)
        return {"status": "completed", "counts": counts}
    except Exception as exc:
        store.upsert_kg_sync_status(run_id, "neo4j", "failed", error=str(exc))
        return {"status": "failed", "error": str(exc)}


def sync_qdrant(store: CorpusStore, run_id: str) -> dict[str, Any]:
    if not settings.qdrant_url:
        store.upsert_kg_sync_status(run_id, "qdrant", "skipped", error="QDRANT_URL is not set")
        return {"status": "skipped", "error": "QDRANT_URL is not set"}
    headers = {"Content-Type": "application/json"}
    if settings.qdrant_api_key:
        headers["api-key"] = settings.qdrant_api_key
    embeddings = store.fetchall("SELECT * FROM kg_embeddings WHERE run_id=?", (run_id,))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in embeddings:
        grouped[item["target_type"]].append(item)
    try:
        counts = {}
        for target_type, rows in grouped.items():
            collection = _qdrant_collection(target_type)
            _qdrant_request(
                "PUT",
                f"{settings.qdrant_url}/collections/{collection}",
                headers,
                {
                    "vectors": {"size": settings.kg_embedding_dimensions, "distance": "Cosine"},
                    "optimizers_config": {"default_segment_number": 2},
                },
            )
            points = []
            for row in rows:
                payload = _loads(row["payload"], {})
                points.append(
                    {
                        "id": int(stable_hash(row["id"]), 16),
                        "vector": _loads(row["embedding"], []),
                        "payload": {**payload, "postgres_id": row["target_id"], "target_type": target_type, "embedding_id": row["id"]},
                    }
                )
            for batch in _chunks(points, 128):
                _qdrant_request("PUT", f"{settings.qdrant_url}/collections/{collection}/points?wait=true", headers, {"points": batch})
            counts[target_type] = len(rows)
        store.upsert_kg_sync_status(run_id, "qdrant", "completed", counts=counts)
        return {"status": "completed", "counts": counts}
    except Exception as exc:
        store.upsert_kg_sync_status(run_id, "qdrant", "failed", error=str(exc))
        return {"status": "failed", "error": str(exc)}


def load_materials_kg_context(run_id: str = "latest", query: str = "", database_url: str | None = None, top_k: int = 8) -> dict[str, Any]:
    store = CorpusStore(database_url)
    store.initialize_schema()
    try:
        actual_run_id = store.latest_run_id() if run_id == "latest" else run_id
        evidence = _fallback_kg_search(store, actual_run_id, query, top_k)
        graph_hits = _graph_hits(store, actual_run_id, query, top_k)
        return {"run_id": actual_run_id, "query": query, "evidence": evidence, "graph_hits": graph_hits}
    finally:
        store.close()


def _fallback_kg_search(store: CorpusStore, run_id: str, query: str, top_k: int) -> list[Evidence]:
    terms = _terms(query)
    rows = []
    rows.extend(
        {
            "kind": "chunk",
            "id": row["id"],
            "source_file_id": row["source_file_id"],
            "text": row["text"],
            "filename": row["source_file_id"],
            "source_type": "chunk",
        }
        for row in store.fetchall("SELECT id, source_file_id, text FROM document_chunks WHERE run_id=?", (run_id,))
    )
    rows.extend(
        {
            "kind": "section",
            "id": row["id"],
            "source_file_id": row["source_file_id"],
            "text": f"{row['title']}\n{row['text']}",
            "filename": row["source_file_id"],
            "source_type": "section",
        }
        for row in store.fetchall("SELECT id, source_file_id, title, text FROM document_sections WHERE run_id=?", (run_id,))
    )
    scored = []
    for row in rows:
        text = row["text"] or ""
        lexical = sum(1 for term in terms if term in text.lower()) / max(1, len(terms))
        if lexical <= 0:
            continue
        scored.append((row, min(1.0, lexical)))
    scored.sort(key=lambda item: item[1], reverse=True)
    evidence: list[Evidence] = []
    for row, score in scored[:top_k]:
        evidence.append(
            Evidence(
                id=f"kg:{row['kind']}:{row['id']}",
                text=row["text"][:900],
                source=SourceRef(source_id=row["source_file_id"], source_type=row["source_type"], filename=row["filename"], section=row["id"]),
                relevance=score,
            )
        )
    return evidence


def _graph_hits(store: CorpusStore, run_id: str, query: str, top_k: int) -> list[dict[str, Any]]:
    terms = _terms(query)
    if not terms:
        return []
    like = [f"%{term}%" for term in terms]
    entity_rows: list[dict[str, Any]] = []
    for pattern in like[:8]:
        entity_rows.extend(
            store.fetchall(
                "SELECT * FROM kg_entities WHERE run_id=? AND (LOWER(name) LIKE ? OR normalized LIKE ?) LIMIT ?",
                (run_id, pattern, pattern, top_k),
            )
        )
    ids = sorted({row["id"] for row in entity_rows})
    hits: list[dict[str, Any]] = []
    for entity_id in ids[:top_k]:
        rels = store.fetchall(
            """
            SELECT kr.*, s.name AS subject_name, o.name AS object_name
            FROM kg_relations kr
            LEFT JOIN kg_entities s ON s.id=kr.subject_entity_id
            LEFT JOIN kg_entities o ON o.id=kr.object_entity_id
            WHERE kr.run_id=? AND (kr.subject_entity_id=? OR kr.object_entity_id=?)
            LIMIT ?
            """,
            (run_id, entity_id, entity_id, top_k),
        )
        hits.extend(rels)
    return hits[:top_k]


def _entity_context_rows(store: CorpusStore, run_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(
        {
            "context_type": "chunk",
            "chunk_id": row["id"],
            "source_file_id": row["source_file_id"],
            "text": row["text"],
        }
        for row in store.fetchall("SELECT id, source_file_id, text FROM document_chunks WHERE run_id=?", (run_id,))
    )
    rows.extend(
        {
            "context_type": "section",
            "chunk_id": row["id"],
            "source_file_id": row["source_file_id"],
            "text": f"{row['title']}\n{row['text']}",
        }
        for row in store.fetchall("SELECT id, source_file_id, title, text FROM document_sections WHERE run_id=?", (run_id,))
    )
    return rows


def _extract_entities_from_text(text: str) -> Iterable[tuple[str, str, float, list[str]]]:
    for match in ALLOY_RE.finditer(text):
        name = _clean_name(match.group(0))
        yield "alloy", name, 0.85, _aliases_for(name)
    for match in FORMULA_RE.finditer(text):
        name = match.group(0)
        if _looks_like_formula(name):
            yield "material", name, 0.7, []
            for element in _elements_in_formula(name):
                yield "element", element, 0.75, []
    low = text.lower()
    for entity_type, terms in TERM_CATALOG.items():
        for term in terms:
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(term.lower())}(?![A-Za-z0-9])", low):
                yield entity_type, term, 0.68, []


def _entities_from_structured_record(run_id: str, source_file_id: str, record_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    entities = []
    if record_type == "materials_project_summary":
        name = str(payload.get("formula_pretty") or payload.get("material_id") or "")
        if name:
            entities.append(_entity(run_id, "material", name, source_file_id, 0.92, canonical_id=payload.get("material_id"), metadata={"source": "materials_project"}))
    elif record_type == "oqmd_formationenergy":
        name = str(payload.get("name") or payload.get("composition") or payload.get("entry_id") or "")
        if name:
            entities.append(_entity(run_id, "material", name, source_file_id, 0.88, canonical_id=payload.get("formationenergy_id"), metadata={"source": "oqmd"}))
    return entities


def _entity(run_id: str, entity_type: str, name: str, source_file_id: str, confidence: float, canonical_id: Any = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": stable_hash(f"{run_id}:{entity_type}:{_normalize(name)}"),
        "entity_type": entity_type,
        "name": name,
        "normalized": _normalize(name),
        "canonical_id": str(canonical_id) if canonical_id else None,
        "confidence": confidence,
        "source_count": 1,
        "first_source_file_id": source_file_id,
        "aliases": _aliases_for(name),
        "metadata": metadata or {},
    }


def _entities_in_text(text: str, by_norm: dict[tuple[str, str], dict[str, Any]]) -> Iterable[dict[str, Any]]:
    low = text.lower()
    seen = set()
    for (entity_type, norm), entity in by_norm.items():
        if len(norm) < 3:
            continue
        if norm in low and entity["id"] not in seen:
            seen.add(entity["id"])
            yield entity


def _predicate_for(entity_type: str) -> str | None:
    return {
        "property": "has_property",
        "method": "studied_by_method",
        "process": "produced_by_process",
        "material_class": "belongs_to_class",
        "phase": "has_phase",
        "defect": "has_defect",
        "application": "mentions_application",
    }.get(entity_type)


def _classes_for_material(name: str) -> list[str]:
    low = name.lower().replace(" ", "")
    classes = []
    if low in {"316l", "304l"}:
        classes.extend(["stainless steel", "austenitic stainless steel"])
    if "ti-6al-4v" in low or "ti6al4v" in low:
        classes.append("titanium alloy")
    if "alsi10mg" in low:
        classes.append("aluminum alloy")
    if "inconel" in low:
        classes.append("nickel superalloy")
    return classes


def _aliases_for(name: str) -> list[str]:
    compact = name.replace("-", "").replace(" ", "")
    aliases = {name, compact}
    if name.lower() == "ti-6al-4v":
        aliases.update({"Ti6Al4V", "Ti 6Al 4V"})
    return sorted(alias for alias in aliases if alias and alias != name)


def _looks_like_formula(name: str) -> bool:
    if name in ELEMENT_SYMBOLS:
        return False
    elements = _elements_in_formula(name)
    return len(elements) >= 2 and all(element in ELEMENT_SYMBOLS for element in elements)


def _elements_in_formula(name: str) -> list[str]:
    return re.findall(r"[A-Z][a-z]?", name)


def _normalize(name: str) -> str:
    return _clean_name(name).lower()


def _clean_name(name: str) -> str:
    return " ".join(name.replace("–", "-").replace("—", "-").split())


def _terms(text: str) -> list[str]:
    return [term for term in re.findall(r"[a-zа-яё0-9][a-zа-яё0-9+\-_/]{1,}", (text or "").lower()) if len(term) > 1]


def _snippet(text: str, needle: str, window: int = 350) -> str:
    low = text.lower()
    pos = low.find((needle or "").lower())
    if pos < 0:
        return text[:window]
    start = max(0, pos - window // 2)
    end = min(len(text), pos + len(needle) + window // 2)
    return text[start:end].strip()


def _neo4j_label(entity_type: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", entity_type) if part) or "Term"


def _qdrant_collection(target_type: str) -> str:
    return f"hf_kg_{target_type}"


def _qdrant_request(method: str, url: str, headers: dict[str, str], payload: dict[str, Any]) -> Any:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Qdrant request failed {exc.code}: {body[:1000]}") from exc


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]
