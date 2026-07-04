from __future__ import annotations

from pathlib import Path

from backend.schemas import PipelineResult


def export_pdf(result: PipelineResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except Exception:
        path.with_suffix(".txt").write_text(_plain_report(result), encoding="utf-8")
        return path.with_suffix(".txt")

    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    y = height - 40
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "Hypothesis Factory Demo Report")
    y -= 30
    c.setFont("Helvetica", 9)
    for line in _plain_report(result).splitlines():
        if y < 50:
            c.showPage()
            y = height - 40
            c.setFont("Helvetica", 9)
        c.drawString(40, y, line[:115])
        y -= 13
    c.save()
    return path


def _plain_report(result: PipelineResult) -> str:
    lines = [
        f"KPI: {result.input.target_kpi}",
        f"Documents: {len(result.knowledge_base.source_documents)}",
        f"Chunks: {len(result.knowledge_base.chunks)}",
        f"Hypotheses: {len(result.hypotheses)}",
        "",
        "Top hypotheses:",
    ]
    for idx, hyp in enumerate(result.hypotheses[:5], 1):
        score = hyp.score_breakdown.final_score if hyp.score_breakdown else 0
        lines.append(f"{idx}. {hyp.title} | score={score:.3f}")
        lines.append(f"   {hyp.expected_effect}")
    return "\n".join(lines)

