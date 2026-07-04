from __future__ import annotations

from pathlib import Path

from backend.config import settings
from backend.schemas import PipelineInput, PipelineResult
from backend.services.entity_extraction import extract_entities
from backend.services.agentic_review import run_agentic_review
from backend.services.export_csv import export_csv
from backend.services.export_json import export_json
from backend.services.export_pdf import export_pdf
from backend.services.hypothesis_generation import generate_hypotheses
from backend.services.corpus_db import load_knowledge_base_from_db
from backend.services.ingestion import ingest_path
from backend.services.knowledge_graph import build_graph, export_graph_json
from backend.services.relation_extraction import extract_relations
from backend.services.retrieval import build_retriever
from backend.services.scoring import rank_hypotheses
from backend.services.validation import validate_hypotheses


def run_pipeline(pipeline_input: PipelineInput) -> PipelineResult:
    if pipeline_input.from_db:
        kb = load_knowledge_base_from_db(pipeline_input.run_id)
    else:
        kb = ingest_path(Path(pipeline_input.data_dir))
    kb.entities = extract_entities(kb)
    kb.relations = extract_relations(kb)
    retriever = build_retriever(kb)
    hypotheses = generate_hypotheses(kb, pipeline_input, retriever)
    hypotheses = run_agentic_review(hypotheses, kb)
    hypotheses = validate_hypotheses(hypotheses, pipeline_input.constraints)
    hypotheses = rank_hypotheses(hypotheses, pipeline_input.weights)
    graph = build_graph(kb, hypotheses)
    graph_path = export_graph_json(graph, settings.output_dir / "graph.json")
    result = PipelineResult(input=pipeline_input, knowledge_base=kb, hypotheses=hypotheses, graph_path=str(graph_path))
    return result


def export_all(result: PipelineResult, output_dir: Path | None = None) -> dict[str, str]:
    output = output_dir or settings.output_dir
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": str(export_json(result, output / "pipeline_result.json")),
        "csv": str(export_csv(result.hypotheses, output / "hypotheses.csv")),
        "pdf": str(export_pdf(result, output / "demo_report.pdf")),
    }
    return paths


if __name__ == "__main__":
    payload = PipelineInput(data_dir=settings.source_data_dir, target_kpi="Снизить потери Ni/Cu в отвальных хвостах на 5%")
    pipeline_result = run_pipeline(payload)
    paths = export_all(pipeline_result)
    print(f"Generated {len(pipeline_result.hypotheses)} hypotheses")
    print(paths)
