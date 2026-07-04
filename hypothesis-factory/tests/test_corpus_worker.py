from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.corpus_worker import main as corpus_worker_main
from backend.corpus_worker import process_jobs, promote_run
from backend.config import settings
from backend.services.chunking import chunk_document
from backend.services.corpus_db import CorpusStore, load_knowledge_base_from_db
from backend.services.materials_project import build_materials_project_document


class CorpusWorkerTests(unittest.TestCase):
    def test_text_ingest_roundtrip_loads_knowledge_base_from_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            root.mkdir()
            (root / "note.txt").write_text("Флотация гидроциклон мельница класс -10 хвосты Ni Cu", encoding="utf-8")
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"

            store = CorpusStore(db_url)
            store.initialize_schema()
            run_id = store.create_run(root, "unit", {"ocr": "off"})
            source_id = store.upsert_source_file(run_id, root, root / "note.txt")
            store.enqueue_job(run_id, "extract", source_id, {"root_path": str(root)})

            processed = process_jobs(store, run_id=run_id, once=False, ocr="off", deepseek="off", repomix="off")
            promote_run(store, run_id)
            store.close()

            kb = load_knowledge_base_from_db(run_id, db_url)
            self.assertEqual(processed, 2)
            self.assertEqual(len(kb.source_documents), 1)
            self.assertGreaterEqual(len(kb.chunks), 1)
            self.assertIn("гидроциклон", kb.chunks[0].text)

    def test_xlsx_ingest_persists_structured_records(self):
        source_xlsx = settings.source_data_dir / "Пример 1" / "Хвосты КГМК.xlsx"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            root.mkdir()
            shutil.copy2(source_xlsx, root / source_xlsx.name)
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"

            store = CorpusStore(db_url)
            store.initialize_schema()
            run_id = store.create_run(root, "xlsx", {"ocr": "off"})
            source_id = store.upsert_source_file(run_id, root, root / source_xlsx.name)
            store.enqueue_job(run_id, "extract", source_id, {"root_path": str(root)})

            process_jobs(store, run_id=run_id, once=False, ocr="off", deepseek="off", repomix="off")
            promote_run(store, run_id)
            status = store.run_status(run_id)
            store.close()

            kb = load_knowledge_base_from_db(run_id, db_url)
            self.assertGreater(status["structured_records"], 0)
            self.assertGreaterEqual(len(kb.summaries), 1)
            self.assertGreaterEqual(len(kb.size_classes), 5)

    def test_materials_project_payload_persists_as_external_source(self):
        payload = {
            "material_id": "mp-13",
            "formula_pretty": "Fe",
            "band_gap": 0.0,
            "energy_above_hull": 0.0,
            "formation_energy_per_atom": -0.12,
            "symmetry": {"symbol": "Im-3m"},
            "structure": {"@module": "pymatgen.core.structure", "lattice": {"a": 2.8}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            store = CorpusStore(db_url)
            store.initialize_schema()
            run_id = store.create_run(Path(tmp) / "materials_project", "mp", {"source": "materials_project"})

            doc = build_materials_project_document(payload)
            source_id = store.upsert_external_source(run_id, "mp-13", "materials_project", doc.title, payload)
            doc = store.save_document(run_id, source_id, doc, {"text_chars": len(doc.text), "ocr_required": False, "extractor": "mp-api"})
            store.replace_structured_records(run_id, source_id, {"materials_project_summary": [payload]})
            store.replace_chunks(run_id, source_id, chunk_document(doc))
            promote_run(store, run_id)
            status = store.run_status(run_id)
            store.close()

            kb = load_knowledge_base_from_db(run_id, db_url)
            self.assertEqual(status["source_files"], 1)
            self.assertEqual(status["structured_records"], 1)
            self.assertEqual(kb.source_documents[0].source_type, "materials_project")
            self.assertIn("Materials Project material_id: mp-13", kb.chunks[0].text)

    def test_materials_project_cli_uses_mp_api_payloads(self):
        payload = {"material_id": "mp-13", "formula_pretty": "Fe", "band_gap": 0.0}
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            with patch("backend.corpus_worker.fetch_materials_project_summaries", return_value=[payload]) as mocked_fetch:
                with redirect_stdout(StringIO()):
                    exit_code = corpus_worker_main(
                        [
                            "--database-url",
                            db_url,
                            "materials-project",
                            "--chemsys",
                            "Fe-Cr-Ni",
                            "--limit",
                            "1",
                            "--run-name",
                            "mp-test",
                        ]
                    )

            store = CorpusStore(db_url)
            store.initialize_schema()
            run_id = store.latest_run_id()
            status = store.run_status(run_id)
            store.close()

            self.assertEqual(exit_code, 0)
            mocked_fetch.assert_called_once()
            self.assertEqual(status["run"]["name"], "mp-test")
            self.assertEqual(status["source_files"], 1)
            self.assertEqual(status["structured_records"], 1)


if __name__ == "__main__":
    unittest.main()
