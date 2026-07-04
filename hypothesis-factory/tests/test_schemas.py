from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.schemas import Evidence, Hypothesis, SourceRef, ValidationStep


class SchemaTests(unittest.TestCase):
    def test_hypothesis_schema(self):
        hyp = Hypothesis(
            id="h1",
            title="test",
            hypothesis_text="text",
            target_kpi="reduce losses",
            proposed_change="adjust hydrocyclone",
            expected_effect="lower tailings",
            material_process_scope="flotation",
            target_element="both",
            causal_chain=["process", "structure", "property"],
            evidence=[
                Evidence(
                    id="e1",
                    text="evidence",
                    source=SourceRef(source_id="s1", source_type="txt", filename="a.txt"),
                    relevance=0.8,
                )
            ],
            novelty_rationale="novel",
            business_value_rationale="valuable",
            validation_plan=[ValidationStep(step="test", success_metric="metric")],
        )
        self.assertEqual(hyp.target_element, "both")


if __name__ == "__main__":
    unittest.main()

