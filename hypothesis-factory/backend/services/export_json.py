from __future__ import annotations

import json
from pathlib import Path

from backend.schemas import PipelineResult


def export_json(result: PipelineResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def export_hypotheses_json(hypotheses, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([h.model_dump(mode="json") for h in hypotheses], ensure_ascii=False, indent=2), encoding="utf-8")
    return path

