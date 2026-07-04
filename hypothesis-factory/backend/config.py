from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def _source_data_dir() -> Path:
    if os.getenv("HF_SOURCE_DATA_DIR"):
        return Path(os.environ["HF_SOURCE_DATA_DIR"])
    docker_path = Path("/workspace/Задача 1")
    if docker_path.exists():
        return docker_path
    return PROJECT_ROOT.parent / "Задача 1"


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    source_data_dir: Path = _source_data_dir()
    output_dir: Path = PROJECT_ROOT / "data" / "demo_outputs"
    corpus_artifacts_dir: Path = PROJECT_ROOT / "data" / "corpus_artifacts"
    corpus_database_url: str | None = os.getenv("CORPUS_DATABASE_URL") or None
    corpus_sqlite_path: Path = Path(os.getenv("CORPUS_SQLITE_PATH", PROJECT_ROOT / "data" / "corpus.db"))
    mock_llm: bool = os.getenv("HF_MOCK_LLM", "1") != "0"
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    deepseek_api_key: str | None = os.getenv("DEEPSEEK_API_KEY") or None
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    deepseek_model_struct: str = os.getenv("DEEPSEEK_MODEL_STRUCT", "deepseek-v4-pro")
    deepseek_model_fast: str = os.getenv("DEEPSEEK_MODEL_FAST", "deepseek-v4-flash")
    glm_api_key: str | None = os.getenv("GLM_API_KEY") or os.getenv("ZAI_API_KEY") or None
    glm_base_url: str = os.getenv("GLM_BASE_URL", "https://api.z.ai/api/paas/v4").rstrip("/")
    glm_model: str = os.getenv("GLM_MODEL", "glm-5.2")
    glm_reasoning_effort: str = os.getenv("GLM_REASONING_EFFORT", "high")
    web_search_enabled: bool = os.getenv("HF_WEB_SEARCH", "1") != "0"
    web_search_max_results: int = int(os.getenv("HF_WEB_SEARCH_MAX", "4"))
    web_search_backends: str = os.getenv("HF_WEB_SEARCH_BACKENDS", "glm,openalex")
    glm_web_search_engine: str = os.getenv("GLM_WEB_SEARCH_ENGINE", "search_prime")
    openalex_web_year_from: int = int(os.getenv("HF_OPENALEX_YEAR_FROM", "2018"))
    pdf_ocr_max_pages: int = int(os.getenv("PDF_OCR_MAX_PAGES", "12"))
    pdf_ocr_dpi: int = int(os.getenv("PDF_OCR_DPI", "200"))
    pdf_ocr_languages: str = os.getenv("PDF_OCR_LANGUAGES", "rus+eng")
    pdf_ocr_min_chars: int = int(os.getenv("PDF_OCR_MIN_CHARS", "800"))
    pdf_ocr_quality_threshold: float = float(os.getenv("PDF_OCR_QUALITY_THRESHOLD", "0.35"))
    ocr_engine: str = (os.getenv("HF_OCR_ENGINE", "tesseract") or "tesseract").strip().lower()
    ocr_gpu: bool = os.getenv("HF_OCR_GPU", "1") != "0"
    materials_project_api_key: str | None = (
        os.getenv("MP_API_KEY")
        or os.getenv("MATERIALS_PROJECT_API_KEY")
        or os.getenv("PMG_MAPI_KEY")
        or os.getenv("materialsproject_api")
        or None
    )
    materials_project_base_url: str = os.getenv("MP_API_BASE_URL", "https://api.materialsproject.org").rstrip("/")
    oqmd_base_url: str = os.getenv("OQMD_BASE_URL", "https://oqmd.org/oqmdapi").rstrip("/")
    oqmd_page_size: int = int(os.getenv("OQMD_PAGE_SIZE", "1000"))
    oqmd_sleep_seconds: float = float(os.getenv("OQMD_SLEEP_SECONDS", "0.1"))
    openalex_api_key: str | None = os.getenv("OPENALEX_API_KEY") or os.getenv("openalex_api_key") or None
    openalex_mailto: str | None = os.getenv("OPENALEX_MAILTO") or os.getenv("openalex_mailto") or None
    unpaywall_email: str | None = os.getenv("UNPAYWALL_EMAIL") or os.getenv("unpaywall_email") or os.getenv("OPENALEX_MAILTO") or os.getenv("openalex_mailto") or None
    chunk_size_chars: int = int(os.getenv("HF_CHUNK_SIZE_CHARS", "1800"))
    chunk_overlap_chars: int = int(os.getenv("HF_CHUNK_OVERLAP_CHARS", "250"))
    neo4j_uri: str | None = os.getenv("NEO4J_URI") or None
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str | None = os.getenv("NEO4J_PASSWORD") or None
    qdrant_url: str | None = (os.getenv("QDRANT_URL") or "").rstrip("/") or None
    qdrant_api_key: str | None = os.getenv("QDRANT_API_KEY") or None
    grobid_url: str | None = (os.getenv("GROBID_URL") or "").rstrip("/") or None
    kg_embedding_model: str = os.getenv("HF_KG_EMBEDDING_MODEL", "local-hashing-384")
    kg_embedding_dimensions: int = int(os.getenv("HF_KG_EMBEDDING_DIMENSIONS", "384"))
    kg_llm_relations: str = os.getenv("HF_KG_LLM_RELATIONS", "off")
    kg_grobid_timeout_seconds: int = int(os.getenv("HF_KG_GROBID_TIMEOUT_SECONDS", "120"))


settings = Settings()
