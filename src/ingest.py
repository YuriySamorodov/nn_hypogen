import argparse
from pathlib import Path

from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.corpus import load_corpus
from src.embeddings import build_embeddings
from src.knowledge_graph import build_knowledge_graph
from src.qdrant_config import qdrant_connection_kwargs
from src.settings import Settings, get_settings


def build_langchain_documents(settings: Settings) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", ". ", " "],
    )

    chunks: list[Document] = []
    for doc in load_corpus(
        include_task_data=settings.include_task_data,
        task_dir=Path(settings.task_data_dir),
        enable_ocr=settings.enable_ocr,
        ocr_languages=settings.ocr_languages,
    ):
        source_documents = splitter.create_documents(
            [doc.text],
            metadatas=[doc.metadata | {"path": str(doc.path)}],
        )
        for index, chunk in enumerate(source_documents):
            chunk.metadata["chunk_index"] = index
            chunks.append(chunk)

    return chunks


def index_demo_corpus(
    settings: Settings | None = None,
    *,
    embeddings=None,
    recreate: bool = True,
) -> QdrantVectorStore:
    settings = settings or get_settings()
    embeddings = embeddings or build_embeddings(settings)
    if settings.include_task_data:
        build_knowledge_graph(settings)
    documents = build_langchain_documents(settings)

    return QdrantVectorStore.from_documents(
        documents,
        embedding=embeddings,
        collection_name=settings.qdrant_collection,
        force_recreate=recreate,
        **qdrant_connection_kwargs(settings),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Index the RAG corpus in Qdrant.")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and recreate the Qdrant collection before indexing.",
    )
    args = parser.parse_args()

    settings = get_settings()
    vector_store = index_demo_corpus(settings, recreate=args.recreate)
    print(
        f"Indexed corpus into Qdrant collection "
        f"{settings.qdrant_collection!r} at {settings.qdrant_url}."
    )
    print(f"Vector store: {type(vector_store).__name__}")


if __name__ == "__main__":
    main()
