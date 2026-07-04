from __future__ import annotations

from backend.schemas import Hypothesis, ScoreBreakdown, ScoringWeights


def final_score(scores: ScoreBreakdown, weights: ScoringWeights) -> float:
    positive = (
        weights.kpi_impact * scores.kpi_impact
        + weights.feasibility * scores.feasibility
        + weights.evidence_strength * scores.evidence_strength
        + weights.causal_consistency * scores.causal_consistency
        + weights.novelty * scores.novelty
        + weights.business_value * scores.business_value
        + weights.implementability * scores.implementability
    )
    penalty = weights.risk * scores.risk
    normalizer = max(
        1e-9,
        weights.kpi_impact
        + weights.feasibility
        + weights.evidence_strength
        + weights.causal_consistency
        + weights.novelty
        + weights.business_value
        + weights.implementability,
    )
    return max(0.0, min(1.0, (positive - penalty) / normalizer))


def score_hypothesis(hypothesis: Hypothesis, max_target_tonnes: float, weights: ScoringWeights) -> Hypothesis:
    tonnes = _extract_target_tonnes(hypothesis)
    kpi = min(1.0, tonnes / max(1.0, max_target_tonnes))
    existing_equipment = any(word in hypothesis.proposed_change.lower() for word in ["настрой", "режим", "насад", "плотност", "вода", "контроль"])
    high_capex = any(word in hypothesis.proposed_change.lower() for word in ["замена", "добавление", "нов", "строитель"])
    evidence_strength = min(1.0, 0.25 + 0.15 * len(hypothesis.evidence))
    risk = 0.35 + (0.25 if high_capex else 0.0) - (0.10 if existing_equipment else 0.0)
    scores = ScoreBreakdown(
        kpi_impact=kpi,
        feasibility=0.85 if existing_equipment else (0.55 if high_capex else 0.70),
        evidence_strength=evidence_strength,
        causal_consistency=0.90 if len(hypothesis.causal_chain) >= 4 else 0.55,
        novelty=0.65 if hypothesis.generator != "expert_seed" else 0.35,
        business_value=min(1.0, 0.35 + kpi * 0.65),
        implementability=0.85 if existing_equipment else 0.55,
        risk=max(0.05, min(1.0, risk)),
        rationale={
            "kpi_impact": f"Оценка по целевому тоннажу {tonnes:.1f} т относительно максимального кандидата.",
            "feasibility": "Настройка существующего оборудования получает преимущество перед CapEx.",
            "evidence_strength": "Основано на количестве локальных evidence-фрагментов.",
            "risk": "Штраф за CapEx, риск шламования, перегрузку и ухудшение селективности.",
        },
    )
    scores.final_score = final_score(scores, weights)
    hypothesis.score_breakdown = scores
    return hypothesis


def rank_hypotheses(hypotheses: list[Hypothesis], weights: ScoringWeights) -> list[Hypothesis]:
    max_tonnes = max([_extract_target_tonnes(h) for h in hypotheses] + [1.0])
    scored = [score_hypothesis(h, max_tonnes, weights) for h in hypotheses]
    return sorted(scored, key=lambda hyp: hyp.score_breakdown.final_score if hyp.score_breakdown else 0.0, reverse=True)


def _extract_target_tonnes(hypothesis: Hypothesis) -> float:
    for evidence in hypothesis.evidence:
        for token in evidence.text.replace(",", ".").split():
            try:
                value = float(token)
            except ValueError:
                continue
            if value > 10:
                return value
    return 100.0

