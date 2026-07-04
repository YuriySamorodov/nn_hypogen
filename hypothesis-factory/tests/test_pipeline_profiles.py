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
        cls.pipeline_input = PipelineInput(data_dir=settings.source_data_dir, target_kpi="Снизить потери Ni/Cu")

    def test_all_18_profiles_are_runnable(self):
        profiles = pipeline_profiles()
        self.assertEqual(len(profiles), 18)
        self.assertTrue({"v16", "v17", "v18"}.issubset({profile.id for profile in profiles}))
        metrics = []
        for profile in profiles:
            profile_input = self.pipeline_input.model_copy(update={"retrieval_mode": profile.retrieval_mode})
            retriever = build_retriever(self.kb, profile_input)
            hypotheses, profile_metrics = run_pipeline_profile(profile, self.kb, profile_input, retriever)
            self.assertGreater(len(hypotheses), 0, profile.id)
            self.assertIn("quality_score", profile_metrics)
            metrics.append(profile_metrics)
        ranked = sorted(metrics, key=lambda row: row["quality_score"], reverse=True)
        self.assertIn(ranked[0]["profile_id"], {"v15", "v18"})


if __name__ == "__main__":
    unittest.main()
