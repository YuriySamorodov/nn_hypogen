from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.main import export_all, run_pipeline
from backend.schemas import PipelineInput


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.source_data_dir
    result = run_pipeline(PipelineInput(data_dir=data_dir, target_kpi="Снизить потери Ni/Cu в отвальных хвостах на 5%"))
    print(export_all(result))


if __name__ == "__main__":
    main()

