# Hypothesis Factory

Интерпретируемый пайплайн для сбора материаловедческих данных, построения corpus/KG и генерации evidence-backed R&D-гипотез.

Система сейчас состоит из нескольких слоев:

```text
raw files / APIs / PDF archives
  -> corpus ingestion
  -> PostgreSQL provenance ledger
  -> PDF/OCR/GROBID text + sections + assets
  -> Materials KG entities/relations/embeddings
  -> Neo4j graph index + Qdrant vector index
  -> search / UI / hypothesis pipeline
```

PostgreSQL остается главным ledger/source-of-truth. Neo4j и Qdrant являются derived indexes: их можно пересобрать из Postgres.

## Быстрый Запуск

Local demo без Docker:

```bash
cd hypothesis-factory
python scripts/run_demo_pipeline.py ../Задача\ 1
python -m unittest discover -s tests
```

Полный Docker stack:

```bash
cd /home/andy/cursorprj/hackNOR
docker compose up -d postgres neo4j qdrant grobid hypothesis-factory
```

Ingest папки в PostgreSQL:

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker ingest \
  --path /workspace/Задача\ 1 \
  --run-name zadacha1 \
  --ocr auto \
  --repomix auto \
  --deepseek auto
```

Сборка Materials KG для уже загруженного run:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id latest \
  --stages sections,assets,entities,relations,embeddings,sync
```

Поиск:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker search \
  --query "316L SLM fatigue porosity biomedical applications" \
  --run-id latest
```

## Разделы Документации

- [Обзор системы](docs/README.md)
- [Corpus worker и PostgreSQL](docs/corpus/README.md)
- [Источники данных: OpenAlex, Materials Project, OQMD, PAN PDF](docs/sources/README.md)
- [PDF, OCR, GROBID и извлечение таблиц/формул](docs/pdf-processing/README.md)
- [Materials KG: Postgres + Neo4j + Qdrant](docs/materials-kg/README.md)
- [Docker stack и переменные окружения](docs/docker/README.md)
- [Pipeline, Streamlit UI и hypothesis generation](docs/pipeline-ui/README.md)
- [Операции, статусы, troubleshooting](docs/operations/README.md)

## Основные Команды

Статус corpus run:

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker status --run-id latest
```

Статус KG:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker status --run-id latest
```

Запуск hypothesis pipeline из DB:

```bash
docker compose run --rm hypothesis-factory python scripts/run_demo_pipeline.py \
  /workspace/Задача\ 1 \
  --from-db \
  --run-id latest
```

Streamlit UI:

```bash
cd hypothesis-factory
streamlit run app/streamlit_app.py
```

Тесты:

```bash
cd hypothesis-factory
python -m unittest discover -s tests
```

## Что Уже Реализовано

- DB-backed corpus worker с PostgreSQL/SQLite fallback.
- Таблицы provenance: `ingest_runs`, `ingest_jobs`, `source_files`, `document_texts`, `document_chunks`, `structured_records`, `artifacts`, `llm_calls`.
- Materials KG schema: `document_sections`, `document_assets`, `kg_entities`, `kg_entity_aliases`, `kg_relations`, `kg_embeddings`, `kg_sync_status`.
- PDF extraction: Poppler `pdftotext -layout`, tables/formulas heuristics, OCR fallback, GROBID TEI для научных PDF.
- External sources: Materials Project, OQMD, OpenAlex/Unpaywall, PAN journals PDF scraper.
- KG worker: sections/assets/entities/relations/embeddings/sync.
- Neo4j graph sync и Qdrant vector sync как derived indexes.
- Fallback KG search из Postgres, если Neo4j/Qdrant недоступны.
- Streamlit вкладка Materials KG search.
- Existing hypothesis generation pipeline сохранен и работает через `--from-db`.

## Важные Ограничения

- Sci-Hub не интегрирован и не используется как ingestion source.
- Neo4j/Qdrant/GROBID могут быть недоступны: система пишет degraded/skipped status и не портит Postgres.
- Local embeddings сейчас deterministic hashing/TF-IDF style, не production-grade scientific embeddings.
- LLM/DeepSeek optional: без API key вызовы логируются как skipped.
- KG extraction v1 rule-based + optional LLM, не полноценная MatKG ontology.
