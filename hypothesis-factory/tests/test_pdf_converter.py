from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.corpus_worker import process_jobs, promote_run
from backend.services.corpus_db import CorpusStore
from backend.services.pdf_converter import PDFConversionResult, extract_formula_records, extract_table_records
from backend.services.pdf_ocr import PDFOCRResult, estimate_ocr_quality, needs_deepseek_ocr_assist
from backend.schemas import SourceDocument


class PDFConverterTests(unittest.TestCase):
    def test_extracts_formula_and_table_records_from_layout_text(self):
        text = "\n".join(
            [
                "Sample equation",
                "E = m c^2",
                "Fe2O3 + 3CO -> 2Fe + 3CO2",
                "",
                "Element    Grade wt%    Recovery %",
                "Ni         0.32         71.5",
                "Cu         0.18         62.0",
            ]
        )

        formulas = extract_formula_records(text)
        tables = extract_table_records(text)

        self.assertGreaterEqual(len(formulas), 2)
        self.assertTrue(any(item["kind"] == "equation" for item in formulas))
        self.assertTrue(any(item["kind"] == "chemical_formula" for item in formulas))
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["row_count"], 3)
        self.assertEqual(tables[0]["column_count"], 3)

    def test_pdf_ingest_persists_formulas_and_tables(self):
        layout_text = "\n".join(
            [
                "Metallurgy PDF",
                "k = A exp(-Ea / R T)",
                "",
                "Stream    Ni %    Cu %",
                "Feed      0.30    0.12",
                "Tailings  0.05    0.02",
            ]
        )
        result = PDFConversionResult(
            text=layout_text,
            metadata={"parser": "test-pdf-converter", "ocr_required": False},
            structured={
                "pdf_formulas": extract_formula_records(layout_text),
                "pdf_tables": extract_table_records(layout_text),
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            root.mkdir()
            pdf_path = root / "paper.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 test")
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"

            store = CorpusStore(db_url)
            store.initialize_schema()
            run_id = store.create_run(root, "pdf", {"ocr": "off"})
            source_id = store.upsert_source_file(run_id, root, pdf_path)
            store.enqueue_job(run_id, "extract", source_id, {"root_path": str(root)})

            with patch("backend.services.pdf_parser.convert_pdf", return_value=result):
                process_jobs(store, run_id=run_id, once=False, ocr="off", deepseek="off", repomix="off")
            promote_run(store, run_id)
            status = store.run_status(run_id)
            records = store.fetchall("SELECT record_type, payload FROM structured_records WHERE run_id=? ORDER BY record_type", (run_id,))
            store.close()

        self.assertEqual(status["source_files"], 1)
        self.assertGreaterEqual(status["structured_records"], 2)
        self.assertIn("pdf_formulas", {row["record_type"] for row in records})
        self.assertIn("pdf_tables", {row["record_type"] for row in records})

    def test_pdf_ocr_quality_routes_low_quality_text_to_deepseek(self):
        poor = PDFOCRResult("??", "completed", {"quality_score": estimate_ocr_quality("??", 1)})
        good = PDFOCRResult("Clean OCR text with Fe2O3 + 3CO -> 2Fe + 3CO2 and enough details." * 30, "completed", {"quality_score": 0.8})

        self.assertTrue(needs_deepseek_ocr_assist(poor, min_chars=100, quality_threshold=0.35))
        self.assertFalse(needs_deepseek_ocr_assist(good, min_chars=100, quality_threshold=0.35))

    def test_pdf_ocr_worker_updates_document_and_structured_records(self):
        ocr_text = "\n".join(
            [
                "[OCR page 1]",
                "E = m c^2",
                "Fe2O3 + 3CO -> 2Fe + 3CO2",
                "",
                "Stream    Ni %    Cu %",
                "Feed      0.30    0.12",
                "Tailings  0.05    0.02",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            root.mkdir()
            pdf_path = root / "scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 scanned")
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"

            store = CorpusStore(db_url)
            store.initialize_schema()
            run_id = store.create_run(root, "pdf-ocr", {"ocr": "auto", "deepseek": "off"})
            source_id = store.upsert_source_file(run_id, root, pdf_path)
            doc = SourceDocument(id=source_id, path=str(pdf_path), source_type="pdf", title="scan.pdf", text="", metadata={"ocr_required": True})
            store.save_document(run_id, source_id, doc, {"text_chars": 0, "ocr_required": True, "extractor": "test"})
            store.enqueue_job(run_id, "ocr", source_id, {"mode": "auto"})

            with patch(
                "backend.corpus_worker.ocr_pdf_with_tesseract",
                return_value=PDFOCRResult(ocr_text, "completed", {"ocr_engine": "test-ocr", "ocr_chars": len(ocr_text), "quality_score": 0.9}),
            ):
                process_jobs(store, run_id=run_id, once=False, ocr="auto", deepseek="off", repomix="off")

            row = store.fetchone("SELECT text, text_quality, metadata FROM document_texts WHERE source_file_id=?", (source_id,))
            records = store.fetchall("SELECT record_type FROM structured_records WHERE run_id=? ORDER BY record_type", (run_id,))
            chunks = store.fetchone("SELECT COUNT(*) AS count FROM document_chunks WHERE run_id=? AND source_file_id=?", (run_id, source_id))
            store.close()

        self.assertIn("Fe2O3", row["text"])
        self.assertFalse(json_loads(row["text_quality"])["ocr_required"])
        self.assertFalse(json_loads(row["metadata"])["ocr_required"])
        self.assertIn("pdf_ocr_formulas", {record["record_type"] for record in records})
        self.assertIn("pdf_ocr_tables", {record["record_type"] for record in records})
        self.assertGreater(chunks["count"], 0)

    def test_pdf_ocr_worker_can_use_deepseek_cleanup_for_poor_local_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            root.mkdir()
            pdf_path = root / "poor-scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 scanned")
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"

            store = CorpusStore(db_url)
            store.initialize_schema()
            run_id = store.create_run(root, "pdf-deepseek-ocr", {"ocr": "auto", "deepseek": "auto"})
            source_id = store.upsert_source_file(run_id, root, pdf_path)
            doc = SourceDocument(id=source_id, path=str(pdf_path), source_type="pdf", title="poor-scan.pdf", text="", metadata={"ocr_required": True})
            store.save_document(run_id, source_id, doc, {"text_chars": 0, "ocr_required": True, "extractor": "test"})
            store.enqueue_job(run_id, "ocr", source_id, {"mode": "auto"})

            improved = "Cleaned OCR: k = A exp(-Ea / R T)\nStream    Ni %    Cu %\nFeed      0.30    0.12"
            with patch(
                "backend.corpus_worker.ocr_pdf_with_tesseract",
                return_value=PDFOCRResult("bad", "completed", {"ocr_engine": "test-ocr", "ocr_chars": 3, "quality_score": 0.05}),
            ), patch(
                "backend.corpus_worker._deepseek_improve_ocr_text",
                return_value={
                    "text": improved,
                    "metadata": {"deepseek_ocr_cleanup": True, "deepseek_model": "test"},
                    "structured": {"deepseek_ocr_structure": [{"text": improved, "quality_notes": ["mock"]}]},
                },
            ):
                process_jobs(store, run_id=run_id, once=False, ocr="auto", deepseek="auto", repomix="off")

            row = store.fetchone("SELECT text, text_quality, metadata FROM document_texts WHERE source_file_id=?", (source_id,))
            records = store.fetchall("SELECT record_type FROM structured_records WHERE run_id=? ORDER BY record_type", (run_id,))
            store.close()

        self.assertIn("Cleaned OCR", row["text"])
        self.assertEqual(json_loads(row["text_quality"])["extractor"], "deepseek_ocr_cleanup")
        self.assertTrue(json_loads(row["metadata"])["ocr_metadata"]["deepseek_ocr_cleanup"])
        self.assertIn("deepseek_ocr_structure", {record["record_type"] for record in records})


def json_loads(value: str):
    import json

    return json.loads(value)


if __name__ == "__main__":
    unittest.main()
