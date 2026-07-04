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
from backend.services.literature import (
    DEFAULT_MATERIALS_QUERY_PROFILES,
    abstract_from_inverted_index,
    get_materials_queries,
    normalize_doi,
)
from export_literature_openalex import _collect_queries, main as export_literature_main


class LiteratureExportTests(unittest.TestCase):
    def test_abstract_from_openalex_inverted_index(self):
        index = {"materials": [0], "interact": [2], "surfaces": [1]}
        self.assertEqual(abstract_from_inverted_index(index), "materials surfaces interact")

    def test_normalize_doi(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1000/ABC"), "10.1000/abc")

    def test_query_profiles_are_available_and_full_is_deduped_union(self):
        for profile in ["core", "adjacent", "mining", "energy", "bio_soft", "computational", "full"]:
            self.assertIn(profile, DEFAULT_MATERIALS_QUERY_PROFILES)
            self.assertGreater(len(DEFAULT_MATERIALS_QUERY_PROFILES[profile]), 0)

        union = []
        for profile, queries in DEFAULT_MATERIALS_QUERY_PROFILES.items():
            if profile != "full":
                union.extend(queries)
        self.assertEqual(get_materials_queries("full"), list(dict.fromkeys(union)))
        self.assertIn("physical chemistry materials", get_materials_queries("adjacent"))
        self.assertIn("hydrometallurgy materials", get_materials_queries("mining"))

    def test_query_overrides_do_not_mix_with_profile(self):
        args = type(
            "Args",
            (),
            {
                "query": ["custom materials", "custom materials"],
                "queries_file": None,
                "profile": "full",
                "default_queries": True,
            },
        )()
        self.assertEqual(_collect_queries(args), ["custom materials"])

    def test_profile_selects_built_in_queries(self):
        args = type(
            "Args",
            (),
            {
                "query": [],
                "queries_file": None,
                "profile": "bio-soft",
                "default_queries": False,
            },
        )()
        self.assertEqual(_collect_queries(args), get_materials_queries("bio_soft"))

    def test_exporter_writes_jsonl_manifest_and_db(self):
        work = {
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.1000/test",
            "title": "Surface chemistry of materials",
            "publication_year": 2025,
            "type": "article",
            "cited_by_count": 7,
            "open_access": {"is_oa": True},
            "abstract_inverted_index": {"Surface": [0], "chemistry": [1]},
            "topics": [{"display_name": "Materials chemistry"}],
            "concepts": [{"display_name": "Materials science"}],
            "keywords": [{"display_name": "surface"}],
        }
        page = {"results": [work], "meta": {"next_cursor": None}}
        unpaywall = {"doi": "10.1000/test", "is_oa": True, "best_oa_location": {"url": "https://example.org/paper"}}
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "lit"
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            argv = [
                "export_literature_openalex.py",
                "--output-dir",
                str(output_dir),
                "--query",
                "materials chemistry",
                "--limit-per-query",
                "1",
                "--limit-total",
                "1",
                "--unpaywall",
                "auto",
                "--ingest-db",
                "--database-url",
                db_url,
            ]
            with (
                patch.object(sys, "argv", argv),
                patch("export_literature_openalex.fetch_openalex_works_page", return_value=page),
                patch("export_literature_openalex.fetch_unpaywall", return_value=unpaywall),
                patch("export_literature_openalex._should_check_unpaywall", return_value=True),
            ):
                with redirect_stdout(StringIO()):
                    exit_code = export_literature_main()

            jsonl_path = output_dir / "openalex_works.jsonl"
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]

            store = CorpusStore(db_url)
            store.initialize_schema()
            status = store.run_status(store.latest_run_id())
            store.close()

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["unpaywall"]["is_oa"], True)
            self.assertEqual(manifest["records_written"], 1)
            self.assertIsNone(manifest["profile"])
            self.assertEqual(manifest["profile_categories"], [])
            self.assertEqual(manifest["unpaywall_mode"], "auto")
            self.assertEqual(manifest["unpaywall_enabled"], True)
            self.assertEqual(status["source_files"], 1)
            self.assertEqual(status["structured_records"], 1)

    def test_profile_manifest_without_unpaywall_email(self):
        work = {
            "id": "https://openalex.org/W2",
            "doi": "https://doi.org/10.1000/profile",
            "title": "Materials profile smoke",
            "publication_year": 2026,
            "type": "article",
            "open_access": {"is_oa": False},
            "abstract_inverted_index": {"Materials": [0]},
        }
        page = {"results": [work], "meta": {"next_cursor": None}}
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "profile"
            argv = [
                "export_literature_openalex.py",
                "--output-dir",
                str(output_dir),
                "--profile",
                "core",
                "--limit-per-query",
                "2",
                "--limit-total",
                "1",
                "--unpaywall",
                "auto",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch("export_literature_openalex.fetch_openalex_works_page", return_value=page),
                patch("export_literature_openalex._should_check_unpaywall", return_value=False),
            ):
                with redirect_stdout(StringIO()):
                    exit_code = export_literature_main()

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in (output_dir / "openalex_works.jsonl").read_text(encoding="utf-8").splitlines()]

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(manifest["profile"], "core")
            self.assertEqual(manifest["profile_categories"], ["core"])
            self.assertEqual(manifest["query_count"], len(get_materials_queries("core")))
            self.assertEqual(manifest["unpaywall_enabled"], False)


if __name__ == "__main__":
    unittest.main()
