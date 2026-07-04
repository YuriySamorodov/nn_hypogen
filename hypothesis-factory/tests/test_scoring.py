from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.schemas import ScoreBreakdown, ScoringWeights
from backend.services.scoring import final_score


class ScoringTests(unittest.TestCase):
    def test_risk_penalty_reduces_score(self):
        weights = ScoringWeights()
        low_risk = ScoreBreakdown(
            kpi_impact=1,
            feasibility=1,
            evidence_strength=1,
            causal_consistency=1,
            novelty=1,
            business_value=1,
            implementability=1,
            risk=0,
        )
        high_risk = low_risk.model_copy(update={"risk": 1})
        self.assertGreater(final_score(low_risk, weights), final_score(high_risk, weights))


if __name__ == "__main__":
    unittest.main()

