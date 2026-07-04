# Документация Hypothesis Factory

Этот каталог описывает текущую систему после добавления corpus ingestion, PostgreSQL ledger, материаловедческих источников, PDF/OCR/GROBID обработки и Materials KG на Neo4j/Qdrant.

## Карта Системы

```text
Sources:
  folder Задача 1
  PAN PDFs
  Materials Project
  OQMD
  OpenAlex + Unpaywall

Ingestion:
  backend.corpus_worker
  scripts/export_*.py
  PostgreSQL source_files/document_texts/document_chunks/structured_records

Document processing:
  Poppler pdftotext
  Tesseract OCR
  GROBID TEI
  DeepSeek optional cleanup

Knowledge:
  document_sections
  document_assets
  kg_entities
  kg_relations
  kg_embeddings

Serving:
  Neo4j graph index
  Qdrant vector index
  Postgres fallback search
  Streamlit UI
  hypothesis pipeline
```

## README По Разделам

- [Corpus worker и PostgreSQL](corpus/README.md)
- [Источники данных](sources/README.md)
- [PDF/OCR/GROBID](pdf-processing/README.md)
- [Materials KG](materials-kg/README.md)
- [Docker](docker/README.md)
- [Pipeline и UI](pipeline-ui/README.md)
- [Operations и troubleshooting](operations/README.md)

## Главный Инвариант

PostgreSQL является системой записи. Все тяжелые индексы считаются производными:

- Neo4j можно пересобрать из `kg_entities` и `kg_relations`.
- Qdrant можно пересобрать из `kg_embeddings`.
- GROBID/DeepSeek outputs сохраняются как artifacts/structured records, а не как единственный источник истины.

Это важно для больших прогонов: если Neo4j/Qdrant/GROBID упали, ingestion не должен терять raw/source provenance.
