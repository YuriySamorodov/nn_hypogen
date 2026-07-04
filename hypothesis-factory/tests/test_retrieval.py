from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.schemas import PipelineInput, SourceDocument
from backend.services.chunking import chunk_document
from backend.services.corpus_db import CorpusStore
from backend.services.materials_kg import build_embeddings
from backend.services.retrieval import KGVectorRetriever, build_retriever


class RetrievalTests(unittest.TestCase):
    def test_build_retriever_tfidf_fallback_without_db_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            run_id, kb = _seed_db(db_url)
            payload = PipelineInput(data_dir=".", target_kpi="Снизить потери Ni/Cu", from_db=False, run_id=run_id, retrieval_mode="auto")
            retriever = build_retriever(kb, payload)
            evidence = retriever.retrieve("hydrocyclone flotation", top_k=2)
            self.assertGreaterEqual(len(evidence), 1)

    def test_kg_vector_retriever_uses_embedding_ledger_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            run_id, kb = _seed_db(db_url)
            retriever = KGVectorRetriever(kb, run_id, "kg", database_url=db_url)
            evidence = retriever.retrieve("316L steel fatigue porosity", top_k=3)
            self.assertGreaterEqual(len(evidence), 1)
            self.assertIn("316L", evidence[0].text)

    def test_qdrant_retriever_uses_run_filtered_payload_and_db_hydration(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            run_id, kb = _seed_db(db_url)
            chunk_id = kb.chunks[0].id
            store = CorpusStore(db_url)
            store.initialize_schema()
            store.upsert_kg_sync_status(run_id, "qdrant", "completed", counts={"chunks": 1})
            store.close()

            fake_settings = SimpleNamespace(qdrant_url="http://qdrant", qdrant_api_key=None, kg_embedding_dimensions=384)

            def fake_qdrant_request(url, headers, payload):
                self.assertEqual(payload["filter"]["must"][0]["key"], "run_id")
                self.assertEqual(payload["filter"]["must"][0]["match"]["value"], run_id)
                if "hf_kg_chunks" not in url:
                    return {"result": []}
                return {
                    "result": [
                        {
                            "score": 0.91,
                            "payload": {
                                "run_id": run_id,
                                "postgres_id": chunk_id,
                                "target_type": "chunks",
                                "source_file_id": "src1",
                            },
                        }
                    ]
                }

            with patch("backend.services.retrieval.settings", fake_settings), patch("backend.services.retrieval._qdrant_request", fake_qdrant_request):
                retriever = KGVectorRetriever(kb, run_id, "qdrant", database_url=db_url)
                evidence = retriever.retrieve("316L fatigue", top_k=3)

            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0].id, f"kg:chunk:{chunk_id}")
            self.assertGreater(evidence[0].relevance, 0.9)


def _seed_db(db_url: str):
    root = Path(db_url.removeprefix("sqlite:///")).parent / "corpus"
    root.mkdir()
    store = CorpusStore(db_url)
    store.initialize_schema()
    run_id = store.create_run(root, "retrieval", {})
    doc = SourceDocument(
        id="src1",
        path=str(root / "note.txt"),
        source_type="txt",
        title="316L flotation note",
        text="316L stainless steel processed by SLM shows fatigue issues caused by porosity. Hydrocyclone flotation context is present.",
        metadata={"relative_path": "note.txt"},
    )
    source_id = store.upsert_external_source(run_id, "note", "openalex", doc.title, {"id": "note"})
    doc = store.save_document(run_id, source_id, doc, {"text_chars": len(doc.text), "ocr_required": False, "extractor": "test"})
    store.replace_chunks(run_id, source_id, chunk_document(doc, size=500, overlap=50))
    build_embeddings(store, run_id)
    store.close()

    from backend.services.corpus_db import load_knowledge_base_from_db

    return run_id, load_knowledge_base_from_db(run_id, db_url)


if __name__ == "__main__":
    unittest.main()
