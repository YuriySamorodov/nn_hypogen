from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backend.services.corpus_db import CorpusStore, load_knowledge_base_from_db
from backend.services.oqmd import build_oqmd_document, oqmd_source_key
from export_oqmd import _fetch_payloads_with_recovery, main as export_oqmd_main


class OQMDExportTests(unittest.TestCase):
    def test_build_document_and_source_key(self):
        payload = {
            "formationenergy_id": 4061142,
            "entry_id": 1216058,
            "calculation_id": 2454,
            "name": "Lu",
            "composition": "Lu1",
            "composition_generic": "A",
            "spacegroup": "R-3m",
            "band_gap": 0.0,
            "delta_e": 0.0125,
            "stability": 0.0125,
        }

        doc = build_oqmd_document(payload)

        self.assertEqual(oqmd_source_key(payload), "4061142")
        self.assertEqual(doc.source_type, "oqmd")
        self.assertIn("OQMD formationenergy_id: 4061142", doc.text)
        self.assertEqual(doc.metadata["license"], "CC BY 4.0")

    def test_exporter_writes_jsonl_manifest_and_optional_db(self):
        payload = {
            "formationenergy_id": 4061142,
            "entry_id": 1216058,
            "calculation_id": 2454,
            "name": "Lu",
            "composition": "Lu1",
            "spacegroup": "R-3m",
            "band_gap": 0.0,
            "delta_e": 0.0125,
            "stability": 0.0125,
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "oqmd"
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            argv = [
                "export_oqmd.py",
                "--output-dir",
                str(output_dir),
                "--all",
                "--limit",
                "1",
                "--page-size",
                "1",
                "--ingest-db",
                "--database-url",
                db_url,
            ]
            with patch.object(sys, "argv", argv), patch("export_oqmd.fetch_oqmd_page", return_value={"data": [payload], "links": {"next": None}}):
                with redirect_stdout(StringIO()):
                    exit_code = export_oqmd_main()

            jsonl_path = output_dir / "oqmd_formationenergy.jsonl"
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]

            store = CorpusStore(db_url)
            store.initialize_schema()
            run_id = store.latest_run_id()
            status = store.run_status(run_id)
            records = store.fetchall("SELECT record_type FROM structured_records WHERE run_id=?", (run_id,))
            store.close()
            kb = load_knowledge_base_from_db(run_id, db_url)

        self.assertEqual(exit_code, 0)
        self.assertEqual(rows, [payload])
        self.assertEqual(manifest["records_written"], 1)
        self.assertEqual(manifest["provider"], "OQMD")
        self.assertEqual(status["source_files"], 1)
        self.assertEqual(status["structured_records"], 1)
        self.assertEqual(records[0]["record_type"], "oqmd_formationenergy")
        self.assertEqual(kb.source_documents[0].source_type, "oqmd")
        self.assertIn("OQMD formationenergy_id", kb.chunks[0].text)

    def test_resume_dedupes_by_formationenergy_id(self):
        existing = {"formationenergy_id": 1, "entry_id": 10, "calculation_id": 20, "composition": "Fe1"}
        duplicate = {"formationenergy_id": 1, "entry_id": 10, "calculation_id": 20, "composition": "Fe1"}
        new = {"formationenergy_id": 2, "entry_id": 11, "calculation_id": 21, "composition": "Ni1"}
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "oqmd"
            output_dir.mkdir()
            jsonl_path = output_dir / "oqmd_formationenergy.jsonl"
            jsonl_path.write_text(json.dumps(existing) + "\n", encoding="utf-8")
            argv = [
                "export_oqmd.py",
                "--output-dir",
                str(output_dir),
                "--all",
                "--limit",
                "2",
                "--page-size",
                "2",
                "--resume",
            ]
            with patch.object(sys, "argv", argv), patch("export_oqmd.fetch_oqmd_page", return_value={"data": [duplicate, new], "links": {"next": None}}) as mocked_fetch:
                with redirect_stdout(StringIO()):
                    exit_code = export_oqmd_main()

            rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual([row["formationenergy_id"] for row in rows], [1, 2])
        self.assertEqual(manifest["existing_records"], 1)
        self.assertEqual(manifest["records_written"], 1)
        mocked_fetch.assert_called_once()
        self.assertEqual(mocked_fetch.call_args.kwargs["offset"], 1)

    def test_recovery_bisects_and_skips_bad_single_offset(self):
        def fake_fetch(**kwargs):
            offset = kwargs["offset"]
            limit = kwargs["limit"]
            if offset <= 10 < offset + limit:
                raise RuntimeError("timeout")
            return {
                "data": [
                    {"formationenergy_id": offset + idx, "composition": f"X{offset + idx}"}
                    for idx in range(limit)
                ]
            }

        failed_pages: list[dict] = []
        with patch("export_oqmd.fetch_oqmd_page", side_effect=fake_fetch):
            payloads, skipped = _fetch_payloads_with_recovery(
                limit=4,
                offset=8,
                fields=["formationenergy_id", "composition"],
                filter_value=None,
                timeout=1,
                continue_on_error=True,
                min_page_size=1,
                failed_pages=failed_pages,
            )

        self.assertEqual(skipped, 1)
        self.assertEqual([item["formationenergy_id"] for item in payloads], [8, 9, 11])
        self.assertTrue(any(item["offset"] == 10 and item["limit"] == 1 for item in failed_pages))


if __name__ == "__main__":
    unittest.main()
