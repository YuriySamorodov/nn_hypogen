from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from backend.schemas import Hypothesis, KnowledgeBase


def build_graph(kb: KnowledgeBase, hypotheses: list[Hypothesis] | None = None) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for summary in kb.summaries:
        plant = f"Plant:{summary.plant}"
        stream = f"Stream:{summary.plant}:{summary.stream}"
        graph.add_node(plant, type="Plant", label=summary.plant)
        graph.add_node(stream, type="TailingsStream", label=summary.stream)
        graph.add_edge(plant, stream, relation="HAS_STREAM")
    for rec in kb.size_classes:
        stream = f"Stream:{rec.plant}:{rec.stream}"
        size = f"SizeClass:{rec.plant}:{rec.stream}:{rec.size_class}"
        graph.add_node(size, type="SizeClass", label=rec.size_class, e28_t=rec.element28_tonnes, e29_t=rec.element29_tonnes)
        graph.add_edge(stream, size, relation="HAS_SIZE_CLASS")
        if rec.element28_tonnes:
            graph.add_edge(size, "KPI:reduce_element28_loss", relation="CONTRIBUTES_TO", tonnes=rec.element28_tonnes)
        if rec.element29_tonnes:
            graph.add_edge(size, "KPI:reduce_element29_loss", relation="CONTRIBUTES_TO", tonnes=rec.element29_tonnes)
    for rel in kb.relations:
        graph.add_node(rel.source, type="EvidenceNode", label=rel.source)
        graph.add_node(rel.target, type="EvidenceNode", label=rel.target)
        graph.add_edge(rel.source, rel.target, relation=rel.relation, confidence=rel.confidence, evidence=rel.evidence_text)
    for hyp in hypotheses or []:
        hid = f"Hypothesis:{hyp.id}"
        graph.add_node(hid, type="Hypothesis", label=hyp.title, score=hyp.score_breakdown.final_score if hyp.score_breakdown else 0)
        if hyp.target_plant:
            graph.add_edge(hid, f"Plant:{hyp.target_plant}", relation="TARGETS")
        if hyp.target_size_class and hyp.target_stream and hyp.target_plant:
            graph.add_edge(hid, f"SizeClass:{hyp.target_plant}:{hyp.target_stream}:{hyp.target_size_class}", relation="TARGETS")
        for evidence in hyp.evidence:
            graph.add_edge(hid, evidence.source.source_id, relation="SUPPORTED_BY", relevance=evidence.relevance)
    return graph


def export_graph_json(graph: nx.MultiDiGraph, path: Path) -> Path:
    data = {
        "nodes": [{"id": node, **attrs} for node, attrs in graph.nodes(data=True)],
        "edges": [
            {"source": src, "target": dst, "key": key, **attrs}
            for src, dst, key, attrs in graph.edges(keys=True, data=True)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

