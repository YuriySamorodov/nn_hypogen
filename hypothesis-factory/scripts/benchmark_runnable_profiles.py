from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.schemas import PipelineInput
from backend.services.corpus_db import load_knowledge_base_from_db
from backend.services.ingestion import ingest_path
from backend.services.pipeline_profiles import pipeline_profiles, run_pipeline_profile
from backend.services.retrieval import build_retriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark all runnable Hypothesis Factory pipeline profiles")
    parser.add_argument("data_dir", nargs="?", default=str(settings.source_data_dir))
    parser.add_argument("--from-db", action="store_true", help="Load normalized corpus from the corpus database")
    parser.add_argument("--run-id", default="latest", help="Corpus ingest run id when --from-db is used")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    from_db = args.from_db
    run_id = args.run_id
    output_dir = PROJECT_ROOT / "benchmarks"
    output_dir.mkdir(parents=True, exist_ok=True)

    kb = load_knowledge_base_from_db(run_id) if from_db else ingest_path(data_dir)
    retriever = build_retriever(kb)
    pipeline_input = PipelineInput(data_dir=data_dir, target_kpi="Снизить потери Ni/Cu", from_db=from_db, run_id=run_id)

    rows = []
    examples = {}
    for profile in pipeline_profiles():
        hypotheses, metrics = run_pipeline_profile(profile, kb, pipeline_input, retriever)
        rows.append(metrics)
        examples[profile.id] = [
            {
                "rank": idx,
                "title": hyp.title,
                "generator": hyp.generator,
                "score": round(hyp.score_breakdown.final_score if hyp.score_breakdown else 0.0, 6),
                "evidence": len(hyp.evidence),
                "warnings": len(hyp.warnings),
            }
            for idx, hyp in enumerate(hypotheses[:3], 1)
        ]

    rows.sort(key=lambda item: item["quality_score"], reverse=True)
    csv_path = output_dir / "runnable_profiles.csv"
    json_path = output_dir / "runnable_profiles_summary.json"
    md_path = output_dir / "runnable_profiles.md"

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "data_dir": str(data_dir),
        "from_db": from_db,
        "run_id": run_id,
        "documents": len(kb.source_documents),
        "chunks": len(kb.chunks),
        "profiles": rows,
        "examples": examples,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")

    print(f"profiles={len(rows)}")
    print(f"best={rows[0]['profile_id']}:{rows[0]['profile_name']}:{rows[0]['quality_score']:.3f}")
    print(csv_path)
    print(json_path)
    print(md_path)


def _markdown(payload: dict) -> str:
    lines = [
        "# Runnable profile benchmark",
        "",
        f"- Documents: `{payload['documents']}`",
        f"- Chunks: `{payload['chunks']}`",
        f"- Source: `{'PostgreSQL corpus' if payload['from_db'] else payload['data_dir']}`",
        "",
        "| Rank | ID | Profile | Quality | Hypotheses | Breadth | Avg score | Evidence | Warnings | Runtime, s |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, row in enumerate(payload["profiles"], 1):
        lines.append(
            f"| {idx} | {row['profile_id']} | {row['profile_name']} | "
            f"{row['quality_score']:.3f} | {row['hypotheses']} | "
            f"{row.get('hypothesis_breadth', 0):.3f} | {row.get('avg_final_score', 0):.3f} | {row.get('evidence_coverage', 0):.3f} | "
            f"{row.get('warning_rate', 0):.3f} | {row['runtime_seconds']:.4f} |"
        )
    lines.extend(["", "## Top examples", ""])
    for row in payload["profiles"][:5]:
        pid = row["profile_id"]
        lines.append(f"### {pid} {row['profile_name']}")
        for item in payload["examples"].get(pid, []):
            lines.append(f"- {item['rank']}. `{item['score']:.3f}` {item['title']} ({item['generator']}, evidence={item['evidence']}, warnings={item['warnings']})")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
