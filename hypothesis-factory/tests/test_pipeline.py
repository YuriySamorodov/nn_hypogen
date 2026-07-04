from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.main import run_pipeline
from backend.schemas import PipelineInput


class PipelineTests(unittest.TestCase):
    def test_pipeline_generates_ranked_hypotheses_from_task_data(self):
        data_dir = settings.source_data_dir
        result = run_pipeline(PipelineInput(data_dir=data_dir, target_kpi="Снизить потери Ni/Cu"))
        self.assertGreaterEqual(len(result.knowledge_base.summaries), 4)
        self.assertGreaterEqual(len(result.knowledge_base.size_classes), 20)
        self.assertGreaterEqual(len(result.hypotheses), 10)
        self.assertIsNotNone(result.hypotheses[0].score_breakdown)
        self.assertGreaterEqual(result.hypotheses[0].score_breakdown.final_score, result.hypotheses[-1].score_breakdown.final_score)


if __name__ == "__main__":
    unittest.main()
