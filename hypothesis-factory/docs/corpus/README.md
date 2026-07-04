# Corpus Worker И PostgreSQL

Corpus worker системно переносит файлы и внешние API records в PostgreSQL. Он нужен, чтобы все извлеченное знание было переиспользуемым: текст, chunks, structured JSON, provenance, OCR status, artifacts и LLM calls.

## Что Хранится В БД

Базовый corpus layer:

- `ingest_runs`: один запуск ingestion/export.
- `ingest_jobs`: очередь стадий обработки.
- `source_files`: каждый файл или внешний source record.
- `document_texts`: полный извлеченный текст, без сжатия.
- `document_chunks`: chunks для retrieval.
- `structured_records`: JSON records из XLSX/PDF/API.
- `entities`, `relations`: старый lightweight layer для demo pipeline.
- `artifacts`: Repomix/GROBID/OCR/DeepSeek outputs.
- `llm_calls`: лог LLM вызовов и ошибок.

KG extension layer:

- `document_sections`: title/abstract/body/references.
- `document_assets`: tables/figures/formulas.
- `kg_entities`: материалы, классы, фазы, свойства, методы, процессы.
- `kg_entity_aliases`: aliases/canonicalization.
- `kg_relations`: typed graph relations with evidence.
- `kg_embeddings`: Postgres ledger для embeddings.
- `kg_sync_status`: статус sync в Neo4j/Qdrant.

## Local SQLite Режим

Подходит для тестов и demo без Docker:

```bash
cd hypothesis-factory
python -m backend.corpus_worker ingest \
  --path ../Задача\ 1 \
  --run-name zadacha1 \
  --ocr auto \
  --repomix auto \
  --deepseek auto

python -m backend.corpus_worker status --run-id latest
python scripts/run_demo_pipeline.py ../Задача\ 1 --from-db --run-id latest
```

SQLite файл по умолчанию:

```text
hypothesis-factory/data/corpus.db
```

## PostgreSQL Режим

Через Docker:

```bash
cd /home/andy/cursorprj/hackNOR
docker compose up -d postgres hypothesis-factory

docker compose run --rm hypothesis-factory python -m backend.corpus_worker ingest \
  --path /workspace/Задача\ 1 \
  --run-name zadacha1 \
  --ocr auto \
  --repomix auto \
  --deepseek auto
```

Локально с внешним Postgres:

```bash
export CORPUS_DATABASE_URL=postgresql://user:password@localhost:5432/hypothesis_factory
python -m backend.corpus_worker ingest --path ../Задача\ 1 --run-name zadacha1
```

## Stages

```text
intake
  -> extract
  -> OCR
  -> repomix_pack
  -> deepseek_structure
  -> chunk/index
  -> entity/relation
  -> promote
```

В текущей реализации `intake` создается командой `ingest`: она сканирует папку, пишет `source_files` и ставит jobs.

## Статус

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker status --run-id latest
```

Важные поля:

- `status=running`: run еще считается активным.
- `status=completed`: все прошло чисто.
- `status=completed_degraded`: есть failed/skipped jobs или artifacts, но corpus пригоден.
- `document_texts`: сколько документов получили текст.
- `document_chunks`: сколько chunks доступно для retrieval.
- `structured_records`: сколько JSON records создано.

## Полный Текст Не Сжимается

`document_texts.text` хранит полный извлеченный текст. Chunks создаются отдельно в `document_chunks`. Для больших raw PDF исходники остаются на диске, а в Postgres хранится `path`, `sha256`, `bytes`, provenance и extracted text.

## Как Возобновлять

Для folder ingestion проще создать новый run. Для больших external exporters используется `--resume`, потому что они пишут JSONL + manifest и умеют пропускать уже записанные records.

Для проверки последнего run:

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker status --run-id latest
```
