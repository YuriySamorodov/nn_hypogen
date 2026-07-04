from __future__ import annotations

import json
from pathlib import Path

from backend.schemas import FeedbackRecord


def load_feedback(path: Path) -> list[FeedbackRecord]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [FeedbackRecord.model_validate(item) for item in raw]


def append_feedback(path: Path, record: FeedbackRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [item.model_dump(mode="json") for item in load_feedback(path)]
    data.append(record.model_dump(mode="json"))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

