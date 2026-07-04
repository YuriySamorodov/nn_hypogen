from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.services.ingestion import ingest_path


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.source_data_dir
    kb = ingest_path(data_dir)
    print(f"documents={len(kb.source_documents)}")
    print(f"chunks={len(kb.chunks)}")
    print(f"summaries={len(kb.summaries)}")
    print(f"size_classes={len(kb.size_classes)}")
    print(f"extractability={len(kb.extractability)}")


if __name__ == "__main__":
    main()

