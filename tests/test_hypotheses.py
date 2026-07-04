import pytest

from src.hypotheses import (
    HypothesisGenerationError,
    apply_grounding_check,
    collect_allowed_source_ids,
    hypotheses_to_markdown,
    parse_hypotheses_json,
    rank_hypotheses,
)
from src.schemas import RetrievedChunk


VALID_JSON = """
{
  "target_property": "повысить жаропрочность",
  "constraints": "ниобий до 0.3%",
  "hypotheses": [
    {
      "title": "Старение с ограниченным Nb",
      "statement": "Добавка 0.3% Nb и старение 760 C повысят жаропрочность.",
      "rationale": "Источник связывает Nb и режим старения с выделениями.",
      "mechanism": "Измельчение упрочняющих выделений и карбидов MC.",
      "novelty_score": 4,
      "feasibility_score": 5,
      "risk_level": "medium",
      "expected_kpi_impact": "+10-15% длительной прочности",
      "evidence": [
        {
          "source_id": "DOC-NI-001",
          "title": "Микролегирование никелевых жаропрочных сплавов",
          "quote": "диапазон 0.1-0.4 мас.% Nb",
          "relevance": "Поддерживает выбранный диапазон легирования."
        }
      ],
      "validation_plan": ["Выплавить образцы", "Провести старение", "Сравнить прочность"]
    }
  ]
}
"""


def test_parse_hypotheses_json_accepts_fenced_json() -> None:
    batch = parse_hypotheses_json(f"```json\n{VALID_JSON}\n```")

    assert batch.target_property == "повысить жаропрочность"
    assert batch.hypotheses[0].novelty_score == 4


def test_parse_hypotheses_json_rejects_broken_json() -> None:
    with pytest.raises(HypothesisGenerationError):
        parse_hypotheses_json("{not json")


def test_rank_hypotheses_prefers_high_score_low_risk() -> None:
    batch = parse_hypotheses_json(VALID_JSON)
    base = batch.hypotheses[0]
    worse = base.model_copy(
        update={
            "title": "Рискованная гипотеза",
            "novelty_score": 5,
            "feasibility_score": 2,
            "risk_level": "high",
        }
    )

    ranked = rank_hypotheses([worse, base])

    assert ranked[0].title == "Старение с ограниченным Nb"


def test_hypotheses_to_markdown_contains_sources_and_validation() -> None:
    batch = parse_hypotheses_json(VALID_JSON)
    markdown = hypotheses_to_markdown(batch)

    assert "DOC-NI-001" in markdown
    assert "План проверки" in markdown


def test_grounding_check_marks_unknown_sources() -> None:
    batch = parse_hypotheses_json(VALID_JSON)
    grounded = apply_grounding_check(batch, {"DOC-NI-001"})

    assert grounded.hypotheses[0].evidence[0].verified is True

    ungrounded = apply_grounding_check(batch, set())
    assert ungrounded.hypotheses[0].evidence[0].verified is False
    assert "не подтверждён" in hypotheses_to_markdown(ungrounded)


def test_collect_allowed_source_ids_includes_chunks_and_facts() -> None:
    chunks = [
        RetrievedChunk(
            source_id="DOC-1",
            title="t",
            language="ru",
            domain="d",
            material="m",
            process="p",
            page_or_section="s",
            text="text",
        )
    ]
    facts = [{"id": "FACT-00001", "text": "example fact"}]

    allowed = collect_allowed_source_ids(chunks, facts)

    assert allowed == {"DOC-1", "FACT-00001"}

