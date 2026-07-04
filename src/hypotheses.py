import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from src.graph_retrieval import format_graph_context
from src.retrieval import format_context
from src.schemas import Hypothesis, HypothesisBatch, RetrievedChunk
from src.settings import Settings, get_settings


class HypothesisGenerationError(RuntimeError):
    pass


RISK_PENALTY = {"low": 0, "medium": 1, "high": 2}


def collect_allowed_source_ids(
    chunks: list[RetrievedChunk],
    graph_facts: list[dict] | None = None,
) -> set[str]:
    allowed = {chunk.source_id for chunk in chunks}
    for fact in graph_facts or []:
        fact_id = fact.get("id")
        if fact_id:
            allowed.add(str(fact_id))
    return allowed


def apply_grounding_check(
    batch: HypothesisBatch,
    allowed_source_ids: set[str],
) -> HypothesisBatch:
    updated: list[Hypothesis] = []
    for hypothesis in batch.hypotheses:
        evidence = [
            item.model_copy(update={"verified": item.source_id in allowed_source_ids})
            for item in hypothesis.evidence
        ]
        updated.append(hypothesis.model_copy(update={"evidence": evidence}))
    return batch.model_copy(update={"hypotheses": updated})


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        return text[first : last + 1]
    return text


def parse_hypotheses_json(raw_text: str) -> HypothesisBatch:
    try:
        payload = json.loads(_strip_json_fences(raw_text))
    except json.JSONDecodeError as exc:
        raise HypothesisGenerationError(
            "DeepSeek returned invalid JSON. Try generation again or reduce the request."
        ) from exc

    try:
        return HypothesisBatch.model_validate(payload)
    except ValidationError as exc:
        raise HypothesisGenerationError(
            "DeepSeek JSON did not match the hypothesis schema."
        ) from exc


def rank_hypotheses(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    return sorted(
        hypotheses,
        key=lambda item: (
            item.novelty_score + item.feasibility_score - RISK_PENALTY[item.risk_level],
            item.feasibility_score,
            item.novelty_score,
        ),
        reverse=True,
    )


def build_generation_prompt(
    *,
    target_property: str,
    constraints: str,
    hypothesis_count: int,
    chunks: list[RetrievedChunk],
    graph_facts: list[dict] | None = None,
) -> list[SystemMessage | HumanMessage]:
    context = format_context(chunks)
    graph_context = format_graph_context(graph_facts or [])
    schema_hint = {
        "target_property": target_property,
        "constraints": constraints,
        "hypotheses": [
            {
                "title": "short title",
                "statement": "testable hypothesis with concrete variables",
                "rationale": "source-grounded explanation",
                "mechanism": "expected physical or process mechanism",
                "novelty_score": 3,
                "feasibility_score": 4,
                "risk_level": "medium",
                "expected_kpi_impact": "expected measurable KPI impact",
                "evidence": [
                    {
                        "source_id": "DOC-NI-001",
                        "title": "source title",
                        "quote": "short supporting fragment",
                        "relevance": "why it supports the hypothesis",
                    }
                ],
                "validation_plan": [
                    "experiment step 1",
                    "experiment step 2",
                    "success/failure criterion",
                ],
            }
        ],
    }

    return [
        SystemMessage(
            content=(
                "You are a scientific RAG assistant for research hypothesis generation. "
                "Use only the supplied context. Do not invent sources. "
                "Treat the knowledge graph facts as higher-trust evidence for numeric values, "
                "tailing classes, mineral loss forms, and example-specific measurements. "
                "If text retrieval and graph facts conflict, prefer graph facts and mention the graph source. "
                "Return strict JSON only, with no Markdown. "
                "Hypotheses must be concrete, testable in a laboratory, and tied to source evidence. "
                "Scores must be integers from 1 to 5. risk_level must be low, medium, or high."
            )
        ),
        HumanMessage(
            content=(
                "Generate research hypotheses for the following task.\n\n"
                f"Target property or technological problem:\n{target_property}\n\n"
                f"Constraints:\n{constraints}\n\n"
                f"Return exactly {hypothesis_count} hypotheses.\n\n"
                "Required JSON shape example:\n"
                f"{json.dumps(schema_hint, ensure_ascii=False, indent=2)}\n\n"
                "Retrieved context:\n"
                f"{context}\n\n"
                "Knowledge graph facts:\n"
                f"{graph_context}"
            )
        ),
    ]


def build_deepseek_llm(settings: Settings) -> ChatOpenAI:
    if not settings.deepseek_api_key:
        raise HypothesisGenerationError(
            "DEEPSEEK_API_KEY is not configured. Copy .env.example to .env and set it."
        )

    return ChatOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        temperature=0.2,
    )


def generate_hypotheses(
    *,
    target_property: str,
    constraints: str,
    chunks: list[RetrievedChunk],
    graph_facts: list[dict] | None = None,
    hypothesis_count: int = 3,
    settings: Settings | None = None,
    llm: ChatOpenAI | None = None,
) -> HypothesisBatch:
    settings = settings or get_settings()
    llm = llm or build_deepseek_llm(settings)
    messages = build_generation_prompt(
        target_property=target_property,
        constraints=constraints,
        hypothesis_count=hypothesis_count,
        chunks=chunks,
        graph_facts=graph_facts,
    )
    response = llm.invoke(messages)
    batch = parse_hypotheses_json(str(response.content))
    allowed_ids = collect_allowed_source_ids(chunks, graph_facts)
    batch = apply_grounding_check(batch, allowed_ids)
    batch.hypotheses = rank_hypotheses(batch.hypotheses)
    return batch


def hypotheses_to_markdown(batch: HypothesisBatch) -> str:
    lines = [
        f"# Гипотезы для задачи: {batch.target_property}",
        "",
        f"**Ограничения:** {batch.constraints}",
        "",
    ]

    for index, hypothesis in enumerate(batch.hypotheses, start=1):
        lines.extend(
            [
                f"## {index}. {hypothesis.title}",
                "",
                f"**Формулировка:** {hypothesis.statement}",
                "",
                f"**Обоснование:** {hypothesis.rationale}",
                "",
                f"**Механизм:** {hypothesis.mechanism}",
                "",
                (
                    f"**Оценки:** новизна {hypothesis.novelty_score}/5, "
                    f"реализуемость {hypothesis.feasibility_score}/5, "
                    f"риск {hypothesis.risk_level}"
                ),
                "",
                f"**Ожидаемый KPI:** {hypothesis.expected_kpi_impact}",
                "",
                "**Источники:**",
            ]
        )
        for evidence in hypothesis.evidence:
            marker = "" if evidence.verified else " ⚠️ *источник не подтверждён*"
            lines.append(
                f"- `{evidence.source_id}` {evidence.title}: "
                f"{evidence.quote} ({evidence.relevance}){marker}"
            )

        lines.extend(["", "**План проверки:**"])
        for step in hypothesis.validation_plan:
            lines.append(f"- {step}")
        lines.append("")

    return "\n".join(lines).strip()
