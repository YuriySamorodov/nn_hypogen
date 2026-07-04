from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.schemas import Hypothesis, KnowledgeBase, PipelineInput, ScoringWeights
from backend.services.agentic_review import run_agentic_review
from backend.services.hypothesis_generation import generate_hypotheses
from backend.services.retrieval import HybridRetriever
from backend.services.scoring import rank_hypotheses
from backend.services.validation import validate_hypotheses


EvidenceMode = Literal["none", "weak", "full"]


@dataclass(frozen=True)
class PipelineProfile:
    id: str
    name: str
    description: str
    enabled_generators: set[str]
    evidence_mode: EvidenceMode = "full"
    use_agentic_review: bool = False
    max_hypotheses: int = 12
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    retrieval_mode: Literal["auto", "tfidf", "kg", "qdrant"] = "tfidf"


def pipeline_profiles() -> list[PipelineProfile]:
    return [
        PipelineProfile("v01", "Single LLM chatbot", "Один генератор без evidence/RAG.", {"expert_seed"}, "none", False, 5),
        PipelineProfile("v02", "Keyword RAG only", "Экспертные seed-гипотезы с ограниченным evidence.", {"expert_seed", "counterfactual"}, "weak", False, 6),
        PipelineProfile("v03", "Dense RAG only", "Смысловой retrieval без числового приоритета.", {"expert_seed", "analogy", "counterfactual"}, "weak", False, 7),
        PipelineProfile("v04", "Hybrid RAG", "Hybrid retrieval и JSON-like hypothesis pack.", {"expert_seed", "analogy", "counterfactual"}, "full", False, 8),
        PipelineProfile("v05", "RAG + numeric Excel priority", "RAG плюс тоннаж потерь из Excel.", {"numeric_priority", "expert_seed"}, "full", False, 10),
        PipelineProfile("v06", "RAG + Knowledge Graph", "Evidence + causal chain без прозрачной формулы.", {"numeric_priority", "analogy", "counterfactual"}, "full", False, 10),
        PipelineProfile(
            "v07",
            "RAG + KG + transparent scoring",
            "Evidence, KG-like causal chain и scoring.",
            {"numeric_priority", "expert_seed", "analogy", "counterfactual"},
            "full",
            False,
            12,
        ),
        PipelineProfile("v08", "Multi-agent only", "Agentic review поверх слабого evidence.", {"expert_seed", "counterfactual"}, "weak", True, 8),
        PipelineProfile("v09", "Multi-agent RAG", "Agentic review + RAG.", {"expert_seed", "analogy", "counterfactual"}, "full", True, 10),
        PipelineProfile("v10", "Multi-agent RAG + KG", "Agentic review + numeric/KG candidates.", {"numeric_priority", "analogy", "counterfactual"}, "full", True, 12),
        PipelineProfile(
            "v11",
            "Physics + process heuristics",
            "Правила флотации/крупности без LLM.",
            {"numeric_priority", "counterfactual"},
            "full",
            False,
            10,
            ScoringWeights(evidence_strength=0.12, causal_consistency=0.18, implementability=0.14, risk=0.18),
        ),
        PipelineProfile("v12", "Predictive ML only", "Prediction-guided кандидаты без богатого evidence.", {"prediction_guided"}, "weak", False, 5),
        PipelineProfile(
            "v13",
            "Digital twin first",
            "Simulation-first proxy: counterfactual + prediction-guided.",
            {"counterfactual", "prediction_guided", "numeric_priority"},
            "weak",
            False,
            8,
        ),
        PipelineProfile("v14", "Computer vision froth control", "CV/APC proxy через схемы и control hypotheses.", {"counterfactual", "expert_seed"}, "weak", False, 6),
        PipelineProfile(
            "v15",
            "Recommended hybrid factory",
            "RAG + KG + numeric priority + multi-agent critique + feedback-ready scoring.",
            {"numeric_priority", "expert_seed", "analogy", "counterfactual", "prediction_guided"},
            "full",
            True,
            18,
        ),
        PipelineProfile(
            "v16",
            "Vector KG RAG",
            "KG embeddings ledger with vector evidence retrieval.",
            {"numeric_priority", "expert_seed", "analogy", "counterfactual", "prediction_guided"},
            "full",
            False,
            18,
            ScoringWeights(evidence_strength=0.18, causal_consistency=0.16, novelty=0.09, risk=0.13),
            "kg",
        ),
        PipelineProfile(
            "v17",
            "Metadata-filtered Vector RAG",
            "Vector evidence with plant/element/size metadata filters.",
            {"numeric_priority", "expert_seed", "analogy", "counterfactual", "prediction_guided"},
            "full",
            False,
            18,
            ScoringWeights(kpi_impact=0.23, evidence_strength=0.18, causal_consistency=0.16, novelty=0.09, risk=0.13),
            "auto",
        ),
        PipelineProfile(
            "v18",
            "Full KG Vector Factory",
            "Vector KG retrieval + metadata filters + agentic review + transparent scoring.",
            {"numeric_priority", "expert_seed", "analogy", "counterfactual", "prediction_guided"},
            "full",
            True,
            18,
            ScoringWeights(kpi_impact=0.23, evidence_strength=0.18, causal_consistency=0.16, business_value=0.11, implementability=0.11, risk=0.13),
            "auto",
        ),
    ]


def run_pipeline_profile(
    profile: PipelineProfile,
    kb: KnowledgeBase,
    pipeline_input: PipelineInput,
    retriever: HybridRetriever,
) -> tuple[list[Hypothesis], dict[str, Any]]:
    started = time.perf_counter()
    candidates = generate_hypotheses(kb, pipeline_input, retriever)
    candidates = [hyp for hyp in candidates if hyp.generator in profile.enabled_generators]
    candidates = [_apply_evidence_policy(hyp, profile.evidence_mode) for hyp in candidates[: profile.max_hypotheses]]

    if profile.use_agentic_review:
        candidates = run_agentic_review(candidates, kb)
    candidates = validate_hypotheses(candidates, pipeline_input.constraints)
    ranked = rank_hypotheses(candidates, profile.weights)
    elapsed = time.perf_counter() - started
    metrics = profile_quality_metrics(profile, ranked, elapsed, kb)
    return ranked, metrics


def profile_quality_metrics(profile: PipelineProfile, hypotheses: list[Hypothesis], runtime_seconds: float, kb: KnowledgeBase) -> dict[str, Any]:
    if not hypotheses:
        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "hypotheses": 0,
            "runtime_seconds": round(runtime_seconds, 6),
            "quality_score": 0.0,
        }

    avg_final = sum((hyp.score_breakdown.final_score if hyp.score_breakdown else 0.0) for hyp in hypotheses) / len(hypotheses)
    evidence_coverage = sum(1 for hyp in hypotheses if hyp.evidence) / len(hypotheses)
    causal_coverage = sum(1 for hyp in hypotheses if len(hyp.causal_chain) >= 4) / len(hypotheses)
    warning_rate = sum(1 for hyp in hypotheses if hyp.warnings) / len(hypotheses)
    generator_diversity = len({hyp.generator for hyp in hypotheses}) / 6.0
    corpus_use = min(1.0, (len(kb.summaries) + len(kb.size_classes) + len(kb.extractability)) / 60.0)
    hypothesis_breadth = min(1.0, len(hypotheses) / max(1, profile.max_hypotheses))
    quality = max(
        0.0,
        min(
            1.0,
            0.30 * avg_final
            + 0.18 * evidence_coverage
            + 0.16 * causal_coverage
            + 0.12 * generator_diversity
            + 0.10 * corpus_use
            + 0.10 * hypothesis_breadth
            - 0.06 * warning_rate,
        ),
    )
    return {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "retrieval_mode": profile.retrieval_mode,
        "hypotheses": len(hypotheses),
        "avg_final_score": round(avg_final, 6),
        "evidence_coverage": round(evidence_coverage, 6),
        "causal_coverage": round(causal_coverage, 6),
        "warning_rate": round(warning_rate, 6),
        "generator_diversity": round(generator_diversity, 6),
        "corpus_use": round(corpus_use, 6),
        "hypothesis_breadth": round(hypothesis_breadth, 6),
        "runtime_seconds": round(runtime_seconds, 6),
        "quality_score": round(quality, 6),
    }


def _apply_evidence_policy(hypothesis: Hypothesis, mode: EvidenceMode) -> Hypothesis:
    hyp = hypothesis.model_copy(deep=True)
    if mode == "none":
        hyp.evidence = []
    elif mode == "weak":
        hyp.evidence = hyp.evidence[:1]
    return hyp
