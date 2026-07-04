from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SourceRef(BaseModel):
    source_id: str
    source_type: str
    filename: str
    page: int | None = None
    sheet_name: str | None = None
    row_number: int | None = None
    section: str | None = None


class SourceDocument(BaseModel):
    id: str
    path: str
    source_type: Literal["pdf", "docx", "txt", "xlsx", "image", "materials_project", "oqmd", "openalex", "unknown"]
    title: str
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    id: str
    document_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    id: str
    text: str
    source: SourceRef
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)


class TailingsSummary(BaseModel):
    plant: str
    stream: str
    dry_metric_tonnes: float | None = None
    element28_grade_pct: float | None = None
    element28_tonnes: float | None = None
    element29_grade_pct: float | None = None
    element29_tonnes: float | None = None
    source: SourceRef


class SizeClassRecord(BaseModel):
    plant: str
    stream: str
    size_class: str
    mass_share_pct: float | None = None
    element28_loss_share_pct: float | None = None
    element28_tonnes: float | None = None
    element29_loss_share_pct: float | None = None
    element29_tonnes: float | None = None
    source: SourceRef


class ExtractabilityRecord(BaseModel):
    plant: str
    stream: str
    extractable: bool
    element28_share_pct: float | None = None
    element28_tonnes: float | None = None
    element29_share_pct: float | None = None
    element29_tonnes: float | None = None
    source: SourceRef


class Entity(BaseModel):
    id: str
    type: str
    name: str
    normalized: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Relation(BaseModel):
    source: str
    relation: str
    target: str
    evidence_text: str
    source_ref: SourceRef
    confidence: float = Field(ge=0.0, le=1.0)


class Constraints(BaseModel):
    forbidden_elements: list[str] = Field(default_factory=list)
    unavailable_equipment: list[str] = Field(default_factory=list)
    max_temperature_c: float | None = None
    max_cost_increase_pct: float | None = None
    prefer_existing_equipment: bool = True
    no_capex: bool = False


class ScoringWeights(BaseModel):
    kpi_impact: float = 0.22
    feasibility: float = 0.15
    evidence_strength: float = 0.15
    causal_consistency: float = 0.15
    novelty: float = 0.08
    business_value: float = 0.10
    implementability: float = 0.10
    risk: float = 0.15

    @field_validator("*")
    @classmethod
    def non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("weights must be non-negative")
        return value


class ScoreBreakdown(BaseModel):
    kpi_impact: float = Field(ge=0.0, le=1.0)
    feasibility: float = Field(ge=0.0, le=1.0)
    evidence_strength: float = Field(ge=0.0, le=1.0)
    causal_consistency: float = Field(ge=0.0, le=1.0)
    novelty: float = Field(ge=0.0, le=1.0)
    business_value: float = Field(ge=0.0, le=1.0)
    implementability: float = Field(ge=0.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(default=0.0)
    rationale: dict[str, str] = Field(default_factory=dict)


class ValidationStep(BaseModel):
    step: str
    success_metric: str
    expected_duration_days: int = Field(default=7, ge=1)


class Hypothesis(BaseModel):
    id: str
    title: str
    hypothesis_text: str
    target_kpi: str
    proposed_change: str
    expected_effect: str
    material_process_scope: str
    target_plant: str | None = None
    target_stream: str | None = None
    target_size_class: str | None = None
    target_element: Literal["element28", "element29", "both"] = "both"
    causal_chain: list[str]
    evidence: list[Evidence] = Field(default_factory=list)
    novelty_rationale: str
    risks: list[str] = Field(default_factory=list)
    business_value_rationale: str
    validation_plan: list[ValidationStep]
    score_breakdown: ScoreBreakdown | None = None
    warnings: list[str] = Field(default_factory=list)
    generator: str = "mock"


class FeedbackRecord(BaseModel):
    hypothesis_id: str
    action: Literal["approve", "reject", "edit_score", "tested_confirmed", "tested_refuted"]
    expert_comment: str = ""
    score_override: float | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class KnowledgeBase(BaseModel):
    source_documents: list[SourceDocument] = Field(default_factory=list)
    chunks: list[DocumentChunk] = Field(default_factory=list)
    summaries: list[TailingsSummary] = Field(default_factory=list)
    size_classes: list[SizeClassRecord] = Field(default_factory=list)
    extractability: list[ExtractabilityRecord] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)


class PipelineInput(BaseModel):
    data_dir: str | Path
    target_kpi: str
    domain: str = "флотационные хвосты Ni/Cu"
    constraints: Constraints = Field(default_factory=Constraints)
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    top_k_evidence: int = 8
    from_db: bool = False
    run_id: str = "latest"


class PipelineResult(BaseModel):
    input: PipelineInput
    knowledge_base: KnowledgeBase
    hypotheses: list[Hypothesis]
    graph_path: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BenchmarkVariant(BaseModel):
    id: str
    name: str
    description: str
    components: list[str]
    interpretability: float = Field(ge=0.0, le=1.0)
    evidence_grounding: float = Field(ge=0.0, le=1.0)
    domain_fit: float = Field(ge=0.0, le=1.0)
    modernity_2026: float = Field(ge=0.0, le=1.0)
    local_runnability: float = Field(ge=0.0, le=1.0)
    implementation_risk: float = Field(ge=0.0, le=1.0)
    estimated_score: float = 0.0
