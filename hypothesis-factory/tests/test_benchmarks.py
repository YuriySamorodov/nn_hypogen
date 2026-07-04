from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.benchmarking import optimize_variants, score_variant, score_variants, solution_variants


class BenchmarkTests(unittest.TestCase):
    def test_18_variants_and_vector_factory_wins(self):
        variants = score_variants(solution_variants())
        self.assertEqual(len(variants), 18)
        self.assertEqual(variants[0].id, "v18")
        self.assertGreater(variants[0].estimated_score, variants[-1].estimated_score)

    def test_iterative_optimizer_runs_100_iterations_for_each_variant(self):
        rows, final_variants = optimize_variants(iterations=100)
        self.assertEqual(len(rows), 1800)
        self.assertEqual(len(final_variants), 18)
        self.assertEqual(final_variants[0].id, "v18")

        initial = {variant.id: score_variant(variant) for variant in solution_variants()}
        seen_iterations: dict[str, list[int]] = {}
        for row in rows:
            self.assertGreaterEqual(row["score_delta"], 0)
            seen_iterations.setdefault(row["variant_id"], []).append(row["iteration"])

        for variant in final_variants:
            self.assertGreaterEqual(variant.estimated_score, initial[variant.id])
            self.assertEqual(seen_iterations[variant.id], list(range(1, 101)))


if __name__ == "__main__":
    unittest.main()
