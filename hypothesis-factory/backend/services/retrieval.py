from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from typing import Any

import numpy as np

from backend.config import settings
from backend.schemas import DocumentChunk, Evidence, KnowledgeBase, PipelineInput, SourceRef
from backend.services.corpus_db import CorpusStore, _loads
from backend.services.embeddings import TextVectorIndex
from backend.services.materials_kg import embed_text


class HybridRetriever:
    def __init__(self, chunks: list[DocumentChunk], metadata_filter: "MetadataFilter | None" = None):
        self.chunks = chunks
        self.index = TextVectorIndex.build([chunk.text for chunk in chunks])
        self.metadata_filter = metadata_filter or MetadataFilter()
        self.last_warnings: list[str] = []

    def retrieve(self, query: str, top_k: int = 8) -> list[Evidence]:
        self.last_warnings = []
        metadata_filter = self.metadata_filter.merged(MetadataFilter.from_text(query))
        vector_hits = dict(self.index.search(query, top_k=top_k * 2))
        keyword_hits = dict(_keyword_rank(self.chunks, query, top_k=top_k * 2))
        ids = set(vector_hits) | set(keyword_hits)
        merged = []
        for idx in ids:
            score = 0.65 * vector_hits.get(idx, 0.0) + 0.35 * keyword_hits.get(idx, 0.0)
            merged.append((idx, score))
        merged.sort(key=lambda item: item[1], reverse=True)
        evidence = self._evidence_from_hits(merged, top_k, filtered=True, metadata_filter=metadata_filter)
        if evidence or metadata_filter.empty:
            return evidence
        self.last_warnings.append("metadata_filter_empty_result: retried without metadata filters")
        return self._evidence_from_hits(merged, top_k, filtered=False, metadata_filter=metadata_filter)

    def _evidence_from_hits(self, hits: list[tuple[int, float]], top_k: int, filtered: bool, metadata_filter: "MetadataFilter") -> list[Evidence]:
        evidence: list[Evidence] = []
        for idx, score in hits:
            chunk = self.chunks[idx]
            if filtered and not metadata_filter.matches(
                text=chunk.text,
                source_type=str(chunk.metadata.get("source_type", "")),
                metadata=chunk.metadata,
            ):
                continue
            evidence.append(
                Evidence(
                    id=f"ev:{chunk.id}",
                    text=chunk.text[:900],
                    source=SourceRef(
                        source_id=chunk.document_id,
                        source_type=str(chunk.metadata.get("source_type", "unknown")),
                        filename=str(chunk.metadata.get("relative_path", chunk.document_id)),
                        section=str(chunk.metadata.get("chunk_index", "")),
                    ),
                    relevance=max(0.0, min(1.0, score)),
                )
            )
            if len(evidence) >= top_k:
                break
        return evidence


class KGVectorRetriever:
    def __init__(
        self,
        kb: KnowledgeBase,
        run_id: str,
        mode: str,
        database_url: str | None = None,
        metadata_filter: "MetadataFilter | None" = None,
    ) -> None:
        self.kb = kb
        self.run_id = run_id
        self.mode = mode
        self.database_url = database_url
        self.metadata_filter = metadata_filter or MetadataFilter()
        self._fallback: HybridRetriever | None = None
        self.last_warnings: list[str] = []
        self._embedding_index: tuple[list[dict[str, Any]], np.ndarray] | None = None

    @property
    def fallback(self) -> HybridRetriever:
        if self._fallback is None:
            self._fallback = HybridRetriever(self.kb.chunks, self.metadata_filter)
        return self._fallback

    def retrieve(self, query: str, top_k: int = 8) -> list[Evidence]:
        self.last_warnings = []
        metadata_filter = self.metadata_filter.merged(MetadataFilter.from_text(query))
        if self.mode in {"auto", "qdrant"} and _qdrant_ready(self.run_id, self.database_url):
            try:
                evidence = self._qdrant_search(query, top_k, filtered=True, metadata_filter=metadata_filter)
                if not evidence and not metadata_filter.empty:
                    self.last_warnings.append("metadata_filter_empty_result: retried qdrant without metadata filters")
                    evidence = self._qdrant_search(query, top_k, filtered=False, metadata_filter=metadata_filter)
                if evidence or self.mode == "qdrant":
                    return evidence
            except Exception as exc:
                self.last_warnings.append(f"qdrant_search_failed: {exc}")
                if self.mode == "qdrant":
                    self.fallback.metadata_filter = metadata_filter
                    return self.fallback.retrieve(query, top_k)
        if self.mode in {"auto", "kg", "qdrant"}:
            evidence = self._kg_search(query, top_k, filtered=True, metadata_filter=metadata_filter)
            if not evidence and not metadata_filter.empty:
                self.last_warnings.append("metadata_filter_empty_result: retried kg without metadata filters")
                evidence = self._kg_search(query, top_k, filtered=False, metadata_filter=metadata_filter)
            if evidence or self.mode == "kg":
                return evidence
        self.fallback.metadata_filter = metadata_filter
        return self.fallback.retrieve(query, top_k)

    def _qdrant_search(self, query: str, top_k: int, filtered: bool, metadata_filter: "MetadataFilter") -> list[Evidence]:
        if not settings.qdrant_url:
            return []
        vector = embed_text(query, settings.kg_embedding_dimensions)
        headers = {"Content-Type": "application/json"}
        if settings.qdrant_api_key:
            headers["api-key"] = settings.qdrant_api_key
        evidence: list[Evidence] = []
        for collection in ["hf_kg_chunks", "hf_kg_documents", "hf_kg_entities"]:
            payload = {
                "vector": vector,
                "limit": top_k,
                "with_payload": True,
                "filter": {"must": [{"key": "run_id", "match": {"value": self.run_id}}]},
            }
            try:
                response = _qdrant_request(f"{settings.qdrant_url}/collections/{collection}/points/search", headers, payload)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue
                raise
            for point in response.get("result", []):
                item_payload = point.get("payload") or {}
                target_type = str(item_payload.get("target_type") or "").rstrip("s")
                target_id = str(item_payload.get("postgres_id") or "")
                if not target_id:
                    continue
                ev = _evidence_for_target(self.run_id, target_type, target_id, float(point.get("score") or 0.0), self.database_url)
                if ev and (not filtered or metadata_filter.matches(text=ev.text, source_type=ev.source.source_type, metadata=item_payload)):
                    evidence.append(ev)
        evidence.sort(key=lambda item: item.relevance, reverse=True)
        return _dedupe_evidence(evidence)[:top_k]

    def _kg_search(self, query: str, top_k: int, filtered: bool, metadata_filter: "MetadataFilter") -> list[Evidence]:
        query_vector = embed_text(query, settings.kg_embedding_dimensions)
        query_norm = _norm(query_vector)
        if not query_norm:
            return []
        index_rows, matrix = self._load_embedding_index()
        if matrix.size == 0:
            return []
        query_array = np.asarray(query_vector, dtype=np.float32) / query_norm
        scores = matrix @ query_array
        order = np.argsort(scores)[::-1][: max(top_k * 8, top_k)]
        evidence: list[Evidence] = []
        for row_idx in order:
            score = float(scores[row_idx])
            if score <= 0:
                continue
            row = index_rows[int(row_idx)]
            target_type = str(row["target_type"]).rstrip("s")
            ev = _evidence_for_target(self.run_id, target_type, str(row["target_id"]), score, self.database_url)
            payload = _loads(row["payload"], {})
            if ev and (not filtered or metadata_filter.matches(text=ev.text, source_type=ev.source.source_type, metadata=payload)):
                evidence.append(ev)
            if len(evidence) >= top_k:
                break
        return _dedupe_evidence(evidence)

    def _load_embedding_index(self) -> tuple[list[dict[str, Any]], np.ndarray]:
        if self._embedding_index is not None:
            return self._embedding_index
        rows: list[dict[str, Any]] = []
        vectors: list[list[float]] = []
        store = CorpusStore(self.database_url)
        store.initialize_schema()
        try:
            for row in store.fetchall("SELECT * FROM kg_embeddings WHERE run_id=?", (self.run_id,)):
                vector = _loads(row["embedding"], [])
                if vector:
                    rows.append(row)
                    vectors.append(vector)
        finally:
            store.close()
        if vectors:
            matrix = np.asarray(vectors, dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1)
            norms[norms == 0] = 1.0
            matrix = matrix / norms[:, None]
        else:
            matrix = np.zeros((0, settings.kg_embedding_dimensions), dtype=np.float32)
        self._embedding_index = (rows, matrix)
        return self._embedding_index


class MetadataFilter:
    def __init__(
        self,
        plants: set[str] | None = None,
        elements: set[str] | None = None,
        size_classes: set[str] | None = None,
        source_types: set[str] | None = None,
    ) -> None:
        self.plants = plants or set()
        self.elements = elements or set()
        self.size_classes = size_classes or set()
        self.source_types = source_types or set()

    @classmethod
    def from_text(cls, value: str) -> "MetadataFilter":
        text = value.lower()
        plants = {plant for plant in ["КГМК", "НОФ Вкр", "НОФ мед", "ТОФ"] if plant.lower() in text}
        elements = set()
        if any(token in text for token in ["элемент 28", "э28", "element28", "ni", "никел"]):
            elements.add("element28")
        if any(token in text for token in ["элемент 29", "э29", "element29", "cu", "мед"]):
            elements.add("element29")
        size_classes = set(re.findall(r"(?:\+|-)\s*\d+(?:\s*\+\s*\d+)?", text))
        source_types = {source for source in ["pdf", "docx", "xlsx", "txt", "openalex", "materials_project", "oqmd"] if source in text}
        return cls(plants=plants, elements=elements, size_classes=size_classes, source_types=source_types)

    def merged(self, other: "MetadataFilter") -> "MetadataFilter":
        return MetadataFilter(
            plants=set(self.plants) | set(other.plants),
            elements=set(self.elements) | set(other.elements),
            size_classes=set(self.size_classes) | set(other.size_classes),
            source_types=set(self.source_types) | set(other.source_types),
        )

    @property
    def empty(self) -> bool:
        return not (self.plants or self.elements or self.size_classes or self.source_types)

    def matches(self, text: str, source_type: str = "", metadata: dict[str, Any] | None = None) -> bool:
        metadata = metadata or {}
        haystack = " ".join([text, source_type, json.dumps(metadata, ensure_ascii=False)]).lower()
        if self.source_types and source_type.lower() not in self.source_types and not any(source in haystack for source in self.source_types):
            return False
        if self.plants and not any(plant.lower() in haystack for plant in self.plants):
            return False
        if self.elements and not any(any(alias in haystack for alias in _element_aliases(element)) for element in self.elements):
            return False
        if self.size_classes and not any(size.lower().replace(" ", "") in haystack.replace(" ", "") for size in self.size_classes):
            return False
        return True


def build_retriever(kb: KnowledgeBase, pipeline_input: PipelineInput | None = None) -> HybridRetriever | KGVectorRetriever:
    metadata_filter = extract_metadata_filter(pipeline_input, kb)
    if pipeline_input is None or not pipeline_input.from_db or pipeline_input.retrieval_mode == "tfidf":
        return HybridRetriever(kb.chunks, metadata_filter)
    run_id = _resolve_run_id(pipeline_input.run_id)
    return KGVectorRetriever(kb, run_id, pipeline_input.retrieval_mode, metadata_filter=metadata_filter)


def extract_metadata_filter(pipeline_input: PipelineInput | None, kb: KnowledgeBase | None = None) -> MetadataFilter:
    if pipeline_input is None:
        return MetadataFilter()
    return MetadataFilter.from_text(f"{pipeline_input.target_kpi} {pipeline_input.domain}")


def _keyword_rank(chunks: list[DocumentChunk], query: str, top_k: int) -> list[tuple[int, float]]:
    terms = [term.lower() for term in query.replace("/", " ").split() if len(term) > 2]
    scored: list[tuple[int, float]] = []
    for idx, chunk in enumerate(chunks):
        text = chunk.text.lower()
        hits = sum(1 for term in terms if term in text)
        if hits:
            scored.append((idx, hits / len(terms)))
    return sorted(scored, key=lambda item: item[1], reverse=True)[:top_k]


def _element_aliases(element: str) -> list[str]:
    if element == "element28":
        return ["element28", "э28", "элемент 28", "ni", "никел"]
    if element == "element29":
        return ["element29", "э29", "элемент 29", "cu", "мед"]
    return [element.lower()]


def _resolve_run_id(run_id: str, database_url: str | None = None) -> str:
    if run_id != "latest":
        return run_id
    store = CorpusStore(database_url)
    store.initialize_schema()
    try:
        return store.latest_run_id()
    finally:
        store.close()


def _qdrant_ready(run_id: str, database_url: str | None = None) -> bool:
    if not settings.qdrant_url:
        return False
    store = CorpusStore(database_url)
    store.initialize_schema()
    try:
        row = store.fetchone(
            "SELECT status FROM kg_sync_status WHERE run_id=? AND target='qdrant' ORDER BY updated_at DESC LIMIT 1",
            (run_id,),
        )
        return bool(row and row["status"] == "completed")
    finally:
        store.close()


def _qdrant_request(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _evidence_for_target(run_id: str, target_type: str, target_id: str, score: float, database_url: str | None = None) -> Evidence | None:
    store = CorpusStore(database_url)
    store.initialize_schema()
    try:
        if target_type == "chunk":
            row = store.fetchone(
                """
                SELECT dc.id, dc.document_id, dc.chunk_index, dc.text, sf.source_type, sf.relative_path
                FROM document_chunks dc JOIN source_files sf ON sf.id=dc.source_file_id
                WHERE dc.run_id=? AND dc.id=?
                """,
                (run_id, target_id),
            )
            if not row:
                return None
            return Evidence(
                id=f"kg:chunk:{row['id']}",
                text=str(row["text"])[:900],
                source=SourceRef(
                    source_id=str(row["document_id"]),
                    source_type=str(row["source_type"]),
                    filename=str(row["relative_path"]),
                    section=str(row["chunk_index"]),
                ),
                relevance=max(0.0, min(1.0, score)),
            )
        if target_type == "document":
            row = store.fetchone(
                """
                SELECT dt.source_file_id, dt.title, dt.text, sf.source_type, sf.relative_path
                FROM document_texts dt JOIN source_files sf ON sf.id=dt.source_file_id
                WHERE dt.run_id=? AND dt.source_file_id=?
                """,
                (run_id, target_id),
            )
            if not row:
                return None
            text = f"{row['title']}\n{row['text']}"
            return Evidence(
                id=f"kg:document:{row['source_file_id']}",
                text=text[:900],
                source=SourceRef(
                    source_id=str(row["source_file_id"]),
                    source_type=str(row["source_type"]),
                    filename=str(row["relative_path"]),
                    section="document",
                ),
                relevance=max(0.0, min(1.0, score)),
            )
        if target_type == "entitie":
            target_type = "entity"
        if target_type == "entity":
            row = store.fetchone("SELECT * FROM kg_entities WHERE run_id=? AND id=?", (run_id, target_id))
            if not row:
                return None
            source_id = str(row.get("first_source_file_id") or row["id"])
            text = f"{row['entity_type']}: {row['name']}. {row.get('description') or ''}".strip()
            return Evidence(
                id=f"kg:entity:{row['id']}",
                text=text[:900],
                source=SourceRef(source_id=source_id, source_type="kg_entity", filename=source_id, section=str(row["id"])),
                relevance=max(0.0, min(1.0, score)),
            )
        return None
    finally:
        store.close()


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return _cosine_with_norms(left, _norm(left), right, _norm(right))


def _cosine_with_norms(left: list[float], left_norm: float, right: list[float], right_norm: float) -> float:
    if not left_norm or not right_norm:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    return dot / (left_norm * right_norm)


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _dedupe_evidence(evidence: list[Evidence]) -> list[Evidence]:
    seen: set[str] = set()
    result: list[Evidence] = []
    for item in evidence:
        if item.id in seen:
            continue
        seen.add(item.id)
        result.append(item)
    return result
