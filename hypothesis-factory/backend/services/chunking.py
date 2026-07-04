from __future__ import annotations

from backend.schemas import DocumentChunk, SourceDocument


def chunk_document(document: SourceDocument, size: int = 1800, overlap: int = 250) -> list[DocumentChunk]:
    text = " ".join((document.text or "").split())
    if not text:
        return []
    chunks: list[DocumentChunk] = []
    start = 0
    idx = 0
    while start < len(text):
        end = min(len(text), start + size)
        chunks.append(
            DocumentChunk(
                id=f"{document.id}:chunk:{idx}",
                document_id=document.id,
                text=text[start:end],
                metadata={**document.metadata, "chunk_index": idx, "source_type": document.source_type},
            )
        )
        if end == len(text):
            break
        start = max(0, end - overlap)
        idx += 1
    return chunks

