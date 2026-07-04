from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.benchmarking import score_variants


def main() -> None:
    output_dir = PROJECT_ROOT / "benchmarks"
    output_dir.mkdir(parents=True, exist_ok=True)
    variants = score_variants()
    csv_path = output_dir / "solution_variants.csv"
    md_path = output_dir / "solution_variants.md"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(variants[0].model_dump().keys()))
        writer.writeheader()
        for variant in variants:
            writer.writerow(variant.model_dump())
    lines = [
        f"# Benchmark: {len(variants)} pipeline solution variants",
        "",
        "| Rank | ID | Variant | Score | Why |",
        "|---:|---|---|---:|---|",
    ]
    for idx, variant in enumerate(variants, 1):
        lines.append(f"| {idx} | {variant.id} | {variant.name} | {variant.estimated_score:.3f} | {variant.description} |")
    lines.append("")
    lines.append(f"Best pipeline: **{variants[0].name}**.")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(md_path)
    print(csv_path)
    print(f"best={variants[0].id}:{variants[0].name}:{variants[0].estimated_score:.3f}")


if __name__ == "__main__":
    main()
