import warnings

from fastembed import TextEmbedding
from langchain_core.embeddings import Embeddings

from src.settings import Settings


class FastEmbedEmbeddings(Embeddings):
    def __init__(self, model_name: str) -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=".*now uses mean pooling instead of CLS embedding.*",
                category=UserWarning,
            )
            self._model = TextEmbedding(model_name=model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def build_embeddings(settings: Settings) -> FastEmbedEmbeddings:
    return FastEmbedEmbeddings(model_name=settings.embedding_model)
