# Materials KG

Materials KG строит многоуровневую базу знаний по материаловедению поверх corpus data.

## Слои

Document layer:

- documents;
- sections;
- abstracts;
- references;
- tables;
- figures;
- formulas;
- DOI/authors metadata.

Entity layer:

- materials;
- material classes;
- alloys;
- elements;
- phases;
- properties;
- methods;
- processing technologies;
- defects;
- applications.

Semantic layer:

- embeddings для documents;
- embeddings для chunks;
- embeddings для entities.

Graph layer:

- `has_property`;
- `studied_by_method`;
- `produced_by_process`;
- `belongs_to_class`;
- `has_phase`;
- `has_defect`;
- `mentions_application`;
- `similar_to`;
- `cites`.

Serving layer:

- PostgreSQL fallback search;
- Neo4j graph index;
- Qdrant vector index;
- Streamlit KG search page.

## Postgres Tables

KG tables:

```text
document_sections
document_assets
kg_entities
kg_entity_aliases
kg_relations
kg_embeddings
kg_sync_status
```

Postgres является source-of-truth для KG. Neo4j/Qdrant можно пересобрать.

## KG Worker

Команда:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id latest \
  --stages sections,assets,entities,relations,embeddings,sync
```

Можно запускать частями:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id <run_id> \
  --stages sections,assets \
  --grobid auto

docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id <run_id> \
  --stages entities,relations,embeddings

docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id <run_id> \
  --stages sync
```

Отключить external sync:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id <run_id> \
  --stages sections,entities,relations,embeddings,sync \
  --neo4j off \
  --qdrant off
```

## Entity Extraction

Файл:

```text
backend/services/materials_kg.py
```

V1 extraction:

- deterministic regex для alloys: `316L`, `304L`, `Ti-6Al-4V`, `AlSi10Mg`, `Inconel ...`;
- chemical formula detection;
- element extraction from formulas;
- term catalogs для properties/methods/processes/phases/defects/applications/classes;
- structured records mapping для Materials Project/OQMD.

Это не финальная ontology. Типы выбраны так, чтобы потом сопоставить их с MatKG/EMMO.

## Relation Extraction

V1 relation extraction:

- co-occurrence rules внутри chunks/sections;
- class rules для известных alloys;
- OpenAlex citations from `referenced_works`;
- optional DeepSeek relation extraction.

DeepSeek режим:

```text
HF_KG_LLM_RELATIONS=off|auto|force
```

По умолчанию `off`. Если включить и нет `DEEPSEEK_API_KEY`, вызов логируется как skipped.

## Embeddings

Сейчас embeddings локальные и deterministic:

```text
HF_KG_EMBEDDING_MODEL=local-hashing-384
HF_KG_EMBEDDING_DIMENSIONS=384
```

Они нужны для:

- Postgres ledger;
- Qdrant sync;
- тестируемого local-first режима без GPU/API.

Production next step: заменить на scientific embedding model, например SPECTER-like или materials-domain encoder, сохранив тот же `kg_embeddings` contract.

## Neo4j Sync

Настройки:

```text
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=hypothesiskg
```

Nodes:

- `Document`;
- `Section`;
- `Chunk`;
- `Entity`;
- typed labels: `Material`, `Alloy`, `Property`, `Method`, `Process`, etc.

Edges:

- document containment: `HAS_SECTION`, `HAS_CHUNK`;
- KG relations: `KG_RELATION` with `predicate`, `confidence`, `evidence_text`.

## Qdrant Sync

Настройки:

```text
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=
```

Collections:

```text
hf_kg_documents
hf_kg_chunks
hf_kg_entities
```

Payload включает:

- `postgres_id`;
- `run_id`;
- `source_file_id`;
- `entity_type`;
- `source_type`;
- `text_preview`.

## Search

Fallback search без внешних сервисов:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker search \
  --query "316L SLM fatigue porosity biomedical applications" \
  --run-id latest
```

Возвращает:

- evidence snippets from chunks/sections;
- graph hits from `kg_relations`;
- source refs для перехода назад к документу/chunk.

## Важный Практический Совет

Не запускай `kg_worker build --run-id latest` во время активного большого exporter run, если `latest` указывает на незавершенный run. Лучше брать конкретный `run_id` из:

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker status --run-id latest
```
