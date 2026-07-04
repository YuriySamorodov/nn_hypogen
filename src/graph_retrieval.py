import json
import re
from pathlib import Path
from typing import Any

from src.settings import Settings, get_settings


TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text) if len(token) > 1}


def load_graph(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    path = Path(settings.knowledge_graph_path)
    if not path.exists():
        return {"nodes": [], "edges": [], "facts": []}
    return json.loads(path.read_text(encoding="utf-8"))


def retrieve_graph_facts(
    query: str,
    settings: Settings | None = None,
    *,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    graph = load_graph(settings)
    query_tokens = _tokens(query)
    if not query_tokens:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for fact in graph.get("facts", []):
        text = fact.get("text", "")
        fact_tokens = _tokens(text)
        overlap = len(query_tokens & fact_tokens)
        score = float(overlap)

        metric = str(fact.get("metric", "")).lower()
        subject = str(fact.get("subject", "")).lower()
        if "хвост" in query_tokens and "хвост" in subject:
            score += 1.5
        if {"крупность", "класс", "гранулометрия"} & query_tokens and "класс" in subject:
            score += 1.5
        if {"потери", "извлекаемый", "извлечение"} & query_tokens and (
            "потер" in metric or "извлекаемый" in subject
        ):
            score += 1.5
        if {"элемент", "28", "29", "никель", "медь"} & query_tokens:
            if fact.get("element"):
                score += 0.5

        if score > 0:
            scored.append((score, fact))

    scored.sort(key=lambda item: (item[0], item[1].get("value") or 0), reverse=True)
    return [fact for _, fact in scored[: top_k or settings.graph_top_k]]


def format_graph_context(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return "No graph facts matched the query."
    return "\n".join(f"[{fact['id']}] {fact['text']}" for fact in facts)

