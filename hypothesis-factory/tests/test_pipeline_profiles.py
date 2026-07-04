from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.schemas import PipelineInput
from backend.services.ingestion import ingest_path
from backend.services.pipeline_profiles import pipeline_profiles, run_pipeline_profile
from backend.services.retrieval import build_retriever


class PipelineProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = ingest_path(settings.source_data_dir)
        cls.retriever = build_retriever(cls.kb)
        cls.pipeline_input = PipelineInput(data_dir=settings.source_data_dir, target_kpi="Снизить потери Ni/Cu")

    def test_all_15_profiles_are_runnable(self):
        profiles = pipeline_profiles()
        self.assertEqual(len(profiles), 15)
        metrics = []
        for profile in profiles:
            hypotheses, profile_metrics = run_pipeline_profile(profile, self.kb, self.pipeline_input, self.retriever)
            self.assertGreater(len(hypotheses), 0, profile.id)
            self.assertIn("quality_score", profile_metrics)
            metrics.append(profile_metrics)
        ranked = sorted(metrics, key=lambda row: row["quality_score"], reverse=True)
        self.assertEqual(ranked[0]["profile_id"], "v15")


if __name__ == "__main__":
    unittest.main()
