from __future__ import annotations

import csv
from pathlib import Path

from backend.schemas import Hypothesis


def export_csv(hypotheses: list[Hypothesis], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rank",
                "id",
                "title",
                "plant",
                "stream",
                "size_class",
                "target_element",
                "final_score",
                "warnings",
            ],
        )
        writer.writeheader()
        for idx, hyp in enumerate(hypotheses, 1):
            writer.writerow(
                {
                    "rank": idx,
                    "id": hyp.id,
                    "title": hyp.title,
                    "plant": hyp.target_plant or "",
                    "stream": hyp.target_stream or "",
                    "size_class": hyp.target_size_class or "",
                    "target_element": hyp.target_element,
                    "final_score": hyp.score_breakdown.final_score if hyp.score_breakdown else "",
                    "warnings": "; ".join(hyp.warnings),
                }
            )
    return path

