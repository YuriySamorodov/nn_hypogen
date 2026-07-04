from __future__ import annotations

from backend.schemas import DocumentChunk, Evidence, KnowledgeBase, SourceRef
from backend.services.embeddings import TextVectorIndex


class HybridRetriever:
    def __init__(self, chunks: list[DocumentChunk]):
        self.chunks = chunks
        self.index = TextVectorIndex.build([chunk.text for chunk in chunks])

    def retrieve(self, query: str, top_k: int = 8) -> list[Evidence]:
        vector_hits = dict(self.index.search(query, top_k=top_k * 2))
        keyword_hits = dict(_keyword_rank(self.chunks, query, top_k=top_k * 2))
        ids = set(vector_hits) | set(keyword_hits)
        merged = []
        for idx in ids:
            score = 0.65 * vector_hits.get(idx, 0.0) + 0.35 * keyword_hits.get(idx, 0.0)
            merged.append((idx, score))
        merged.sort(key=lambda item: item[1], reverse=True)
        evidence: list[Evidence] = []
        for idx, score in merged[:top_k]:
            chunk = self.chunks[idx]
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
        return evidence


def build_retriever(kb: KnowledgeBase) -> HybridRetriever:
    return HybridRetriever(kb.chunks)


def _keyword_rank(chunks: list[DocumentChunk], query: str, top_k: int) -> list[tuple[int, float]]:
    terms = [term.lower() for term in query.replace("/", " ").split() if len(term) > 2]
    scored: list[tuple[int, float]] = []
    for idx, chunk in enumerate(chunks):
        text = chunk.text.lower()
        hits = sum(1 for term in terms if term in text)
        if hits:
            scored.append((idx, hits / len(terms)))
    return sorted(scored, key=lambda item: item[1], reverse=True)[:top_k]

