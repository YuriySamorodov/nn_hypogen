from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from backend.schemas import ExtractabilityRecord, SizeClassRecord, SourceRef, TailingsSummary


SIZE_CLASS_KEYS = {"+125", "+71", "-125+71", "-71+45", "-45+20", "-20+10", "-10"}
SUMMARY_LABELS = {"Отвальные хвосты", "Хвосты породные", "Хвосты пирротиновые", "Хвосты отвальные"}


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and pd.notna(value):
        return float(value)
    return None


def _label(value: Any) -> str:
    if pd.isna(value):
        return ""
    return " ".join(str(value).replace("мкм", "").strip().split())


def _plant_from_path(path: Path) -> str:
    parent = path.parent.name
    stem = path.stem
    if "КГМК" in stem:
        return "КГМК"
    if "Вкр" in stem or "вкр" in stem:
        return "НОФ Вкр"
    if "мед" in stem.lower():
        return "НОФ мед"
    if "ТОФ" in stem:
        return "ТОФ"
    return parent


def parse_tailings_excel(path: Path) -> tuple[list[TailingsSummary], list[SizeClassRecord], list[ExtractabilityRecord]]:
    summaries: list[TailingsSummary] = []
    size_classes: list[SizeClassRecord] = []
    extractability: list[ExtractabilityRecord] = []
    plant = _plant_from_path(path)
    with pd.ExcelFile(path) as xl:
        sheets = list(xl.sheet_names)

    for sheet in sheets:
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        current_stream = "отвальные хвосты"
        for row_idx, row in df.iterrows():
            label = _label(row.iloc[1] if len(row) > 1 else None)
            compact = label.replace(" ", "")
            source = SourceRef(
                source_id=f"{path.name}:{sheet}:{row_idx + 1}",
                source_type="xlsx",
                filename=str(path),
                sheet_name=sheet,
                row_number=row_idx + 1,
                section=current_stream,
            )

            if label in SUMMARY_LABELS:
                current_stream = label
                summaries.append(
                    TailingsSummary(
                        plant=plant,
                        stream=label,
                        dry_metric_tonnes=_num(row.iloc[2]),
                        element28_grade_pct=_num(row.iloc[3]),
                        element28_tonnes=_num(row.iloc[4]),
                        element29_grade_pct=_num(row.iloc[5]),
                        element29_tonnes=_num(row.iloc[6]),
                        source=source,
                    )
                )
                continue

            if compact in SIZE_CLASS_KEYS and _num(row.iloc[2]) is not None:
                size_classes.append(
                    SizeClassRecord(
                        plant=plant,
                        stream=current_stream,
                        size_class=label,
                        mass_share_pct=_num(row.iloc[2]),
                        element28_loss_share_pct=_num(row.iloc[3]),
                        element28_tonnes=_num(row.iloc[4]),
                        element29_loss_share_pct=_num(row.iloc[5]),
                        element29_tonnes=_num(row.iloc[6]),
                        source=source,
                    )
                )
                continue

            if label.startswith("Итого извлекаемый металл") or label.startswith("Итого не извлекаемый металл"):
                extractability.append(
                    ExtractabilityRecord(
                        plant=plant,
                        stream=current_stream,
                        extractable=label.startswith("Итого извлекаемый"),
                        element28_share_pct=_num(row.iloc[3]),
                        element28_tonnes=_num(row.iloc[4]),
                        element29_share_pct=_num(row.iloc[5]),
                        element29_tonnes=_num(row.iloc[6]),
                        source=source,
                    )
                )
    return summaries, size_classes, extractability
