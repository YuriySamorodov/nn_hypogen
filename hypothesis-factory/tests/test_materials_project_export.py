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

from backend.services.corpus_db import CorpusStore
from export_materials_project import main as export_materials_project_main


class MaterialsProjectExportTests(unittest.TestCase):
    def test_exporter_writes_jsonl_manifest_and_optional_db(self):
        payload = {"material_id": "mp-13", "formula_pretty": "Fe", "band_gap": 0.0}
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "mp"
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            argv = [
                "export_materials_project.py",
                "--output-dir",
                str(output_dir),
                "--chemsys",
                "Fe-Cr-Ni",
                "--limit",
                "1",
                "--ingest-db",
                "--database-url",
                db_url,
            ]
            with patch.object(sys, "argv", argv), patch("export_materials_project.fetch_materials_project_page", return_value=[payload]):
                with redirect_stdout(StringIO()):
                    exit_code = export_materials_project_main()

            jsonl_path = output_dir / "materials_project_summary.jsonl"
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]

            store = CorpusStore(db_url)
            store.initialize_schema()
            status = store.run_status(store.latest_run_id())
            store.close()

            self.assertEqual(exit_code, 0)
            self.assertEqual(rows, [payload])
            self.assertEqual(manifest["records_written"], 1)
            self.assertEqual(status["source_files"], 1)
            self.assertEqual(status["structured_records"], 1)


if __name__ == "__main__":
    unittest.main()
