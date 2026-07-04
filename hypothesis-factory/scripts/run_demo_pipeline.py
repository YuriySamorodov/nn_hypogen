from __future__ import annotations

import sys
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.main import export_all, run_pipeline
from backend.schemas import PipelineInput


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Hypothesis Factory demo pipeline")
    parser.add_argument("data_dir", nargs="?", default=str(settings.source_data_dir))
    parser.add_argument("--from-db", action="store_true", help="Load normalized corpus from the corpus database")
    parser.add_argument("--run-id", default="latest", help="Corpus ingest run id when --from-db is used")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    result = run_pipeline(
        PipelineInput(
            data_dir=data_dir,
            target_kpi="Снизить потери Ni/Cu в отвальных хвостах на 5%",
            from_db=args.from_db,
            run_id=args.run_id,
        )
    )
    exports = export_all(result)
    print(f"documents={len(result.knowledge_base.source_documents)}")
    print(f"chunks={len(result.knowledge_base.chunks)}")
    print(f"hypotheses={len(result.hypotheses)}")
    for idx, hyp in enumerate(result.hypotheses[:10], 1):
        print(f"{idx}. {hyp.score_breakdown.final_score:.3f} | {hyp.title}")
    print(exports)


if __name__ == "__main__":
    main()
