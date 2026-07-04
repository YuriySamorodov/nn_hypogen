from src.export import batch_to_docx_bytes, batch_to_markdown_bytes, write_export_files
from src.hypotheses import parse_hypotheses_json

VALID_JSON = """
{
  "target_property": "повысить жаропрочность",
  "constraints": "ниобий до 0.3%",
  "hypotheses": [
    {
      "title": "Старение с Nb",
      "statement": "Добавка 0.3% Nb повысит жаропрочность.",
      "rationale": "Источник связывает Nb с выделениями.",
      "mechanism": "Карбиды MC.",
      "novelty_score": 4,
      "feasibility_score": 5,
      "risk_level": "medium",
      "expected_kpi_impact": "+10-15%",
      "evidence": [
        {
          "source_id": "DOC-NI-001",
          "title": "Микролегирование",
          "quote": "0.1-0.4 мас.% Nb",
          "relevance": "Поддерживает диапазон."
        }
      ],
      "validation_plan": ["Выплавить образцы", "Сравнить прочность"]
    }
  ]
}
"""


def test_batch_to_markdown_bytes() -> None:
    batch = parse_hypotheses_json(VALID_JSON)
    content = batch_to_markdown_bytes(batch).decode("utf-8")

    assert "DOC-NI-001" in content
    assert "План проверки" in content


def test_batch_to_docx_bytes() -> None:
    batch = parse_hypotheses_json(VALID_JSON)
    content = batch_to_docx_bytes(batch)

    assert content.startswith(b"PK")
    assert len(content) > 1000


def test_write_export_files(tmp_path) -> None:
    batch = parse_hypotheses_json(VALID_JSON)
    md_path, docx_path = write_export_files(batch, tmp_path)

    assert md_path.exists()
    assert docx_path.exists()
    assert md_path.suffix == ".md"
    assert docx_path.suffix == ".docx"
