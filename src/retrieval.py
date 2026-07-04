from langchain_qdrant import QdrantVectorStore

from src.embeddings import build_embeddings
from src.qdrant_config import qdrant_connection_kwargs
from src.schemas import RetrievedChunk
from src.settings import Settings, get_settings


def get_vector_store(settings: Settings | None = None, *, embeddings=None) -> QdrantVectorStore:
    settings = settings or get_settings()
    embeddings = embeddings or build_embeddings(settings)
    return QdrantVectorStore.from_existing_collection(
        embedding=embeddings,
        collection_name=settings.qdrant_collection,
        **qdrant_connection_kwargs(settings),
    )


def retrieve_context(
    query: str,
    settings: Settings | None = None,
    *,
    embeddings=None,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    settings = settings or get_settings()
    vector_store = get_vector_store(settings, embeddings=embeddings)
    results = vector_store.similarity_search_with_score(
        query,
        k=top_k or settings.retrieval_top_k,
    )

    chunks: list[RetrievedChunk] = []
    for document, score in results:
        metadata = document.metadata
        chunks.append(
            RetrievedChunk(
                source_id=str(metadata.get("source_id", "unknown")),
                title=str(metadata.get("title", "Untitled source")),
                language=str(metadata.get("language", "unknown")),
                domain=str(metadata.get("domain", "unknown")),
                material=str(metadata.get("material", "unknown")),
                process=str(metadata.get("process", "unknown")),
                page_or_section=str(metadata.get("page_or_section", "unknown")),
                text=document.page_content,
                score=float(score),
            )
        )

    return chunks


def format_context(chunks: list[RetrievedChunk]) -> str:
    blocks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        blocks.append(
            "\n".join(
                [
                    f"[{index}] {chunk.source_id} | {chunk.title}",
                    f"domain={chunk.domain}; material={chunk.material}; process={chunk.process}",
                    f"section={chunk.page_or_section}; score={chunk.score}",
                    chunk.text,
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)
