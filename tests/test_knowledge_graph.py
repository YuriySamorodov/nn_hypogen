from pathlib import Path

import pytest

from src.graph_retrieval import retrieve_graph_facts
from src.knowledge_graph import build_knowledge_graph
from src.settings import Settings

TASK_XLSX = Path("Задача 1/Пример 1/Хвосты КГМК.xlsx")
COMMITTED_GRAPH = Path("data/knowledge_graph/graph.json")


def _task_excel_ready() -> bool:
    if not TASK_XLSX.exists():
        return False
    return TASK_XLSX.read_bytes()[:2] == b"PK"


@pytest.mark.skipif(not _task_excel_ready(), reason="Excel files not pulled from Git LFS")
def test_build_knowledge_graph_from_excel(tmp_path: Path) -> None:
    graph_path = tmp_path / "graph.json"
    settings = Settings(
        task_data_dir="Задача 1",
        knowledge_graph_path=str(graph_path),
    )

    graph = build_knowledge_graph(settings)

    assert graph_path.exists()
    assert graph["nodes"]
    assert graph["edges"]
    assert graph["facts"]
    assert any("класс" in fact["subject"].lower() for fact in graph["facts"])


@pytest.mark.skipif(not COMMITTED_GRAPH.exists(), reason="Committed graph.json is missing")
def test_retrieve_graph_facts_matches_tailings_query() -> None:
    settings = Settings(
        knowledge_graph_path=str(COMMITTED_GRAPH),
        graph_top_k=5,
    )

    facts = retrieve_graph_facts(
        "потери элемент 28 в хвостах по классам крупности",
        settings,
    )

    assert facts
    assert any(fact.get("element") == "Элемент 28" for fact in facts)
