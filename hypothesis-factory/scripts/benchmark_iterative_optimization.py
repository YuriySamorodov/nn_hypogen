from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.benchmarking import optimize_variants, score_variant, solution_variants


def main() -> None:
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    output_dir = PROJECT_ROOT / "benchmarks"
    output_dir.mkdir(parents=True, exist_ok=True)

    rows, final_variants = optimize_variants(iterations=iterations)
    initial = {variant.id: score_variant(variant) for variant in solution_variants()}

    csv_path = output_dir / "iterative_optimization.csv"
    json_path = output_dir / "iterative_optimization_summary.json"
    md_path = output_dir / "iterative_optimization.md"

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "iterations_per_variant": iterations,
        "total_iterations": len(rows),
        "final_ranking": [
            {
                "rank": idx,
                "id": variant.id,
                "name": variant.name,
                "initial_score": round(initial[variant.id], 6),
                "final_score": round(variant.estimated_score, 6),
                "score_gain": round(variant.estimated_score - initial[variant.id], 6),
                "components": variant.components,
            }
            for idx, variant in enumerate(final_variants, 1)
        ],
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(summary), encoding="utf-8")

    print(f"iterations={len(rows)}")
    print(f"best={final_variants[0].id}:{final_variants[0].name}:{final_variants[0].estimated_score:.3f}")
    print(csv_path)
    print(json_path)
    print(md_path)


def _markdown(summary: dict) -> str:
    lines = [
        f"# Iterative benchmark optimization: {len(summary['final_ranking'])} variants x {summary['iterations_per_variant']} iterations",
        "",
        f"Это воспроизводимый архитектурный benchmark-improve loop. Он не утверждает, что все {len(summary['final_ranking'])} production pipeline полностью реализованы; он фиксирует, какие улучшения дают наибольший marginal gain по выбранным метрикам и как меняется рейтинг после заданных шагов улучшения каждого варианта.",
        "",
        f"- Iterations per variant: `{summary['iterations_per_variant']}`",
        f"- Total benchmark/improvement iterations: `{summary['total_iterations']}`",
        "",
        "| Rank | ID | Variant | Initial | Final | Gain |",
        "|---:|---|---|---:|---:|---:|",
    ]
    for item in summary["final_ranking"]:
        lines.append(
            f"| {item['rank']} | {item['id']} | {item['name']} | "
            f"{item['initial_score']:.3f} | {item['final_score']:.3f} | {item['score_gain']:.3f} |"
        )

    winner = summary["final_ranking"][0]
    lines.extend(
        [
            "",
            f"Best final pipeline: **{winner['id']} {winner['name']}**.",
            "",
            "Практический вывод: сильнее всего растут варианты, где LLM/agents не заменяют доказательную базу, а работают поверх corpus DB, Excel priority, KG, constraints, OCR/provenance и expert feedback.",
            "",
            "Файлы:",
            "",
            f"- `benchmarks/iterative_optimization.csv`: все {summary['total_iterations']} строк benchmark before/after.",
            "- `benchmarks/iterative_optimization_summary.json`: машинно-читаемая финальная сводка.",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
