from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from backend.schemas import BenchmarkVariant


SCORE_WEIGHTS = {
    "interpretability": 0.22,
    "evidence_grounding": 0.22,
    "domain_fit": 0.22,
    "modernity_2026": 0.14,
    "local_runnability": 0.12,
    "implementation_risk": -0.08,
}


@dataclass(frozen=True)
class ImprovementAction:
    name: str
    description: str
    metric_deltas: dict[str, float]
    component: str


IMPROVEMENT_ACTIONS = [
    ImprovementAction(
        "source_provenance",
        "Добавить строгие source refs, hash/provenance и проверку пустых OCR/PDF фрагментов.",
        {"evidence_grounding": 0.050, "interpretability": 0.018, "implementation_risk": -0.010},
        "Provenance",
    ),
    ImprovementAction(
        "numeric_priority",
        "Усилить приоритизацию по тоннажу потерь, крупности и извлекаемым минералогическим формам.",
        {"domain_fit": 0.052, "evidence_grounding": 0.030, "interpretability": 0.014},
        "Numeric priority",
    ),
    ImprovementAction(
        "kg_causal_edges",
        "Добавить причинные ребра process -> particle size -> mineral form -> loss -> KPI.",
        {"interpretability": 0.045, "domain_fit": 0.030, "modernity_2026": 0.014},
        "KG causal edges",
    ),
    ImprovementAction(
        "agentic_critique",
        "Добавить роли генератор/критик/ранжировщик/evolution без обхода scoring.",
        {"modernity_2026": 0.055, "evidence_grounding": 0.018, "implementation_risk": 0.006},
        "Agentic critique",
    ),
    ImprovementAction(
        "local_fallbacks",
        "Убрать обязательные внешние зависимости, добавить deterministic fallback и fixture tests.",
        {"local_runnability": 0.060, "implementation_risk": -0.018},
        "Local fallback",
    ),
    ImprovementAction(
        "expert_feedback_loop",
        "Добавить экспертные approve/reject/tested labels в последующее ранжирование.",
        {"domain_fit": 0.028, "evidence_grounding": 0.022, "modernity_2026": 0.024},
        "Expert feedback",
    ),
    ImprovementAction(
        "ocr_structuring",
        "Улучшить OCR/табличную структуризацию для схем, регламентов и сканных PDF.",
        {"evidence_grounding": 0.042, "local_runnability": 0.018, "implementation_risk": -0.006},
        "OCR structuring",
    ),
    ImprovementAction(
        "risk_constraints",
        "Добавить hard constraints: CAPEX, реагенты, температура, доступное оборудование.",
        {"interpretability": 0.020, "domain_fit": 0.040, "implementation_risk": -0.012},
        "Constraint validation",
    ),
]


def solution_variants() -> list[BenchmarkVariant]:
    raw = [
        ("v01", "Single LLM chatbot", "Один prompt без RAG и числовых проверок.", ["LLM"], .15, .10, .25, .45, .90, .80),
        ("v02", "Keyword RAG only", "Поиск по документам и генерация ответа без графа и scoring.", ["BM25", "LLM"], .35, .55, .45, .45, .85, .45),
        ("v03", "Dense RAG only", "Embedding retrieval без учета точных классов крупности.", ["Embeddings", "LLM"], .35, .50, .40, .55, .70, .45),
        ("v04", "Hybrid RAG", "BM25 + dense retrieval + JSON hypothesis prompt.", ["BM25", "Embeddings", "LLM"], .50, .70, .55, .65, .75, .40),
        ("v05", "RAG + numeric Excel priority", "RAG плюс ранжирование по тоннажу потерь из Excel.", ["RAG", "Excel parser", "Scoring"], .70, .78, .75, .65, .85, .30),
        ("v06", "RAG + Knowledge Graph", "Evidence chunks и PSP/KPI-граф без предиктивной модели.", ["RAG", "NetworkX KG"], .82, .78, .78, .75, .80, .35),
        ("v07", "RAG + KG + transparent scoring", "Граф, evidence и прозрачная weighted scoring формула.", ["RAG", "KG", "Scoring"], .90, .85, .85, .78, .82, .28),
        ("v08", "Multi-agent only", "Генератор, критик и ранжировщик без структурных данных.", ["Agents", "LLM"], .50, .45, .50, .82, .55, .65),
        ("v09", "Multi-agent RAG", "Co-Scientist style debate поверх RAG.", ["Agents", "RAG", "LLM"], .70, .78, .70, .90, .55, .55),
        ("v10", "Multi-agent RAG + KG", "Agents, RAG, KG, но без явного numeric priority.", ["Agents", "RAG", "KG"], .84, .82, .78, .92, .55, .52),
        ("v11", "Physics + process heuristics", "Правила флотации/крупности без LLM.", ["Rules", "Excel parser"], .88, .65, .80, .55, .95, .25),
        ("v12", "Predictive ML only", "Модель извлечения по историческим данным без объяснения.", ["ML"], .35, .35, .45, .70, .65, .65),
        ("v13", "Digital twin first", "Полный цифровой двойник фабрики.", ["Simulator", "ML", "APC"], .80, .70, .90, .95, .20, .85),
        ("v14", "Computer vision froth control", "CV по пене и online setpoints.", ["CV", "APC", "ML"], .65, .55, .72, .85, .35, .75),
        ("v15", "Recommended hybrid factory", "RAG + KG + numeric priority + multi-agent critique + expert feedback + optional LLM.", ["Hybrid RAG", "KG", "Scoring", "Agents", "Feedback"], .94, .90, .92, .95, .78, .32),
        ("v16", "Vector KG RAG", "KG embeddings ledger with vector evidence retrieval.", ["KG embeddings", "Vector RAG", "Scoring"], .90, .90, .86, .88, .82, .34),
        ("v17", "Metadata-filtered Vector RAG", "Vector retrieval plus plant/element/size metadata filters.", ["KG embeddings", "Metadata filters", "Qdrant"], .92, .93, .90, .90, .78, .36),
        ("v18", "Full KG Vector Factory", "Vector KG retrieval + metadata filters + agentic review + transparent scoring.", ["KG embeddings", "Qdrant", "Metadata filters", "Agents", "Scoring"], .96, .95, .94, .96, .74, .34),
    ]
    return [
        BenchmarkVariant(
            id=item[0],
            name=item[1],
            description=item[2],
            components=item[3],
            interpretability=item[4],
            evidence_grounding=item[5],
            domain_fit=item[6],
            modernity_2026=item[7],
            local_runnability=item[8],
            implementation_risk=item[9],
        )
        for item in raw
    ]


def score_variants(variants: list[BenchmarkVariant] | None = None) -> list[BenchmarkVariant]:
    variants = variants or solution_variants()
    for variant in variants:
        variant.estimated_score = score_variant(variant)
    return sorted(variants, key=lambda item: item.estimated_score, reverse=True)


def score_variant(variant: BenchmarkVariant) -> float:
    return max(
        0.0,
        min(
            1.0,
            SCORE_WEIGHTS["interpretability"] * variant.interpretability
            + SCORE_WEIGHTS["evidence_grounding"] * variant.evidence_grounding
            + SCORE_WEIGHTS["domain_fit"] * variant.domain_fit
            + SCORE_WEIGHTS["modernity_2026"] * variant.modernity_2026
            + SCORE_WEIGHTS["local_runnability"] * variant.local_runnability
            + SCORE_WEIGHTS["implementation_risk"] * variant.implementation_risk,
        ),
    )


def optimize_variants(iterations: int = 100) -> tuple[list[dict[str, Any]], list[BenchmarkVariant]]:
    """Run a deterministic benchmark-improve loop for all pipeline variants.

    Each iteration benchmarks the current variant, chooses the action with the
    largest weighted marginal gain, applies a damped improvement, then benchmarks
    the updated variant. This is a reproducible architecture benchmark, not a
    claim that all 15 pipelines are fully implemented production systems.
    """

    rows: list[dict[str, Any]] = []
    final_variants: list[BenchmarkVariant] = []
    for base in solution_variants():
        variant = deepcopy(base)
        variant.estimated_score = score_variant(variant)
        applied_components = set(variant.components)
        for iteration in range(1, iterations + 1):
            before_score = score_variant(variant)
            action = _choose_action(variant, iteration)
            _apply_action(variant, action, iteration)
            if action.component not in applied_components:
                variant.components.append(action.component)
                applied_components.add(action.component)
            after_score = score_variant(variant)
            rows.append(
                {
                    "variant_id": variant.id,
                    "variant_name": variant.name,
                    "iteration": iteration,
                    "action": action.name,
                    "action_description": action.description,
                    "before_score": round(before_score, 6),
                    "after_score": round(after_score, 6),
                    "score_delta": round(after_score - before_score, 6),
                    "interpretability": round(variant.interpretability, 6),
                    "evidence_grounding": round(variant.evidence_grounding, 6),
                    "domain_fit": round(variant.domain_fit, 6),
                    "modernity_2026": round(variant.modernity_2026, 6),
                    "local_runnability": round(variant.local_runnability, 6),
                    "implementation_risk": round(variant.implementation_risk, 6),
                }
            )
            variant.estimated_score = after_score
        final_variants.append(variant)
    return rows, sorted(final_variants, key=lambda item: item.estimated_score, reverse=True)


def _choose_action(variant: BenchmarkVariant, iteration: int) -> ImprovementAction:
    best_action = IMPROVEMENT_ACTIONS[0]
    best_gain = -1.0
    for idx, action in enumerate(IMPROVEMENT_ACTIONS):
        # Rotate ties so every variant receives repeated multi-dimensional
        # improvements instead of overfitting only one metric.
        tie_bias = ((iteration + idx + int(variant.id[1:])) % len(IMPROVEMENT_ACTIONS)) * 0.000001
        gain = _estimated_action_gain(variant, action) + tie_bias
        if gain > best_gain:
            best_gain = gain
            best_action = action
    return best_action


def _estimated_action_gain(variant: BenchmarkVariant, action: ImprovementAction) -> float:
    gain = 0.0
    for metric, base_delta in action.metric_deltas.items():
        weight = SCORE_WEIGHTS[metric]
        current = getattr(variant, metric)
        if metric == "implementation_risk":
            effective = abs(base_delta) * max(current, 0.02)
            gain += abs(weight) * effective
        else:
            effective = base_delta * max(1.0 - current, 0.02)
            gain += weight * effective
    return gain


def _apply_action(variant: BenchmarkVariant, action: ImprovementAction, iteration: int) -> None:
    damping = 1.0 / (1.0 + (iteration - 1) / 35.0)
    for metric, base_delta in action.metric_deltas.items():
        current = getattr(variant, metric)
        if metric == "implementation_risk":
            delta = -abs(base_delta) * max(current, 0.02) * damping
            setattr(variant, metric, _clamp(current + delta))
        else:
            delta = base_delta * max(1.0 - current, 0.02) * damping
            setattr(variant, metric, _clamp(current + delta))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
