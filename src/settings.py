from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL"
    )
    deepseek_model: str = Field(default="deepseek-v4-flash", alias="DEEPSEEK_MODEL")

    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_collection: str = Field(
        default="hypothesis_factory_demo", alias="QDRANT_COLLECTION"
    )

    embedding_model: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        alias="EMBEDDING_MODEL",
    )
    chunk_size: int = Field(default=800, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=120, alias="CHUNK_OVERLAP")
    retrieval_top_k: int = Field(default=6, alias="RETRIEVAL_TOP_K")
    graph_top_k: int = Field(default=10, alias="GRAPH_TOP_K")
    include_task_data: bool = Field(default=True, alias="INCLUDE_TASK_DATA")
    task_data_dir: str = Field(default="Задача 1", alias="TASK_DATA_DIR")
    knowledge_graph_path: str = Field(
        default="data/knowledge_graph/graph.json",
        alias="KNOWLEDGE_GRAPH_PATH",
    )
    enable_ocr: bool = Field(default=True, alias="ENABLE_OCR")
    ocr_languages: str = Field(default="rus+eng", alias="OCR_LANGUAGES")
    export_dir: str = Field(default="exports", alias="EXPORT_DIR")


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    return Settings()
