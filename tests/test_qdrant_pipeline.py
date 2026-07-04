from langchain_core.embeddings import Embeddings

from src.ingest import index_demo_corpus
from src.settings import Settings


class TinyEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        lowered = text.lower()
        return [
            float("ниоб" in lowered or "nb" in lowered),
            float("шихт" in lowered or "себестоим" in lowered),
            float("флотац" in lowered or "медь" in lowered),
        ]


def test_qdrant_collection_created_and_returns_source_metadata() -> None:
    settings = Settings(
        qdrant_url=":memory:",
        qdrant_collection="test_hypothesis_factory_demo",
        chunk_size=500,
        chunk_overlap=50,
        include_task_data=False,
    )

    vector_store = index_demo_corpus(
        settings,
        embeddings=TinyEmbeddings(),
        recreate=True,
    )
    results = vector_store.similarity_search("ниобий жаропрочный сплав", k=2)

    assert vector_store.client.collection_exists(settings.qdrant_collection)
    assert results
    assert results[0].metadata["source_id"].startswith("DOC-")
    assert results[0].metadata["title"]
