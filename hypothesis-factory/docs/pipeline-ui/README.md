# Pipeline И UI

Этот раздел описывает текущий hypothesis pipeline и Streamlit UI.

## Hypothesis Pipeline

Главная точка:

```text
backend/main.py
```

Flow:

```text
PipelineInput
  -> ingest_path OR load_knowledge_base_from_db
  -> extract_entities
  -> extract_relations
  -> HybridRetriever
  -> generate_hypotheses
  -> agentic_review
  -> validation
  -> scoring
  -> NetworkX graph export
  -> JSON/CSV/PDF export
```

Local folder mode:

```bash
cd hypothesis-factory
python scripts/run_demo_pipeline.py ../Задача\ 1
```

DB mode:

```bash
cd hypothesis-factory
python scripts/run_demo_pipeline.py ../Задача\ 1 --from-db --run-id latest
```

В Docker:

```bash
docker compose run --rm hypothesis-factory python scripts/run_demo_pipeline.py \
  /workspace/Задача\ 1 \
  --from-db \
  --run-id latest
```

## KnowledgeBase Contract

`load_knowledge_base_from_db` возвращает:

- `source_documents`;
- `chunks`;
- `summaries`;
- `size_classes`;
- `extractability`.

Это старый demo-compatible contract. Materials KG живет рядом в новых таблицах и читается через:

```text
load_materials_kg_context(run_id, query)
```

## Retrieval

Старый hypothesis retrieval:

```text
backend/services/retrieval.py
```

Он использует:

- local TF-IDF vector search;
- keyword fallback;
- evidence refs.

KG search:

```text
backend/services/materials_kg.py
```

Он использует:

- chunks/sections fallback search из Postgres;
- `kg_relations` graph hits;
- future Neo4j/Qdrant search path.

## Streamlit UI

Запуск локально:

```bash
cd hypothesis-factory
streamlit run app/streamlit_app.py
```

В UI две вкладки:

- `Hypotheses`: генерация гипотез.
- `Materials KG`: поиск по KG context.

Для `Hypotheses` можно включить:

```text
Use corpus DB
Pipeline run_id=latest
```

Для `Materials KG`:

```text
KG run_id
KG query
KG top-k
```

## Экспорт

Pipeline пишет:

```text
data/demo_outputs/pipeline_result.json
data/demo_outputs/hypotheses.csv
data/demo_outputs/demo_report.pdf
data/demo_outputs/graph.json
```

Команда:

```bash
python scripts/export_demo_report.py ../Задача\ 1
```

## Тесты

```bash
cd hypothesis-factory
python -m unittest discover -s tests
```

На момент внедрения KG layer:

```text
Ran 30 tests ... OK
```
