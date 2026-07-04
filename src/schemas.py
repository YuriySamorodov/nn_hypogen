from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Evidence(BaseModel):
    source_id: str = Field(description="Stable source identifier from the corpus.")
    title: str = Field(description="Human-readable source title.")
    quote: str = Field(description="Short source-backed fragment or paraphrase.")
    relevance: str = Field(description="Why this source supports the hypothesis.")
    verified: bool = Field(
        default=True,
        description="Whether source_id was found in retrieved context.",
    )


class Hypothesis(BaseModel):
    title: str
    statement: str
    rationale: str
    mechanism: str
    novelty_score: int = Field(ge=1, le=5)
    feasibility_score: int = Field(ge=1, le=5)
    risk_level: Literal["low", "medium", "high"]
    expected_kpi_impact: str
    evidence: list[Evidence] = Field(min_length=1)
    validation_plan: list[str] = Field(min_length=1)

    @field_validator("title", "statement", "rationale", "mechanism")
    @classmethod
    def require_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field must not be empty")
        return value


class HypothesisBatch(BaseModel):
    target_property: str
    constraints: str
    hypotheses: list[Hypothesis] = Field(min_length=1)


class RetrievedChunk(BaseModel):
    source_id: str
    title: str
    language: str
    domain: str
    material: str
    process: str
    page_or_section: str
    text: str
    score: float | None = None

