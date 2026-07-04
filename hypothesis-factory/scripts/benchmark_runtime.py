from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.main import run_pipeline
from backend.schemas import PipelineInput
from backend.services.ingestion import ingest_path


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.source_data_dir
    output = PROJECT_ROOT / "benchmarks" / "runtime_benchmark.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    kb = ingest_path(data_dir)
    t1 = time.perf_counter()
    result = run_pipeline(PipelineInput(data_dir=data_dir, target_kpi="Снизить потери Ni/Cu"))
    t2 = time.perf_counter()

    payload = {
        "data_dir": str(data_dir),
        "documents": len(kb.source_documents),
        "chunks": len(kb.chunks),
        "summaries": len(kb.summaries),
        "size_classes": len(kb.size_classes),
        "extractability": len(kb.extractability),
        "hypotheses": len(result.hypotheses),
        "ingestion_seconds": round(t1 - t0, 4),
        "full_pipeline_seconds": round(t2 - t1, 4),
        "total_seconds": round(t2 - t0, 4),
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(output)


if __name__ == "__main__":
    main()
