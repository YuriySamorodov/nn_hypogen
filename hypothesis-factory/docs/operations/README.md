# Operations И Troubleshooting

Практические команды для контроля процессов, статусов и возобновления прогонов.

## Посмотреть Статус Последнего Run

Corpus:

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker status --run-id latest
```

KG:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker status --run-id latest
```

## Посмотреть Таблицы В Postgres

```bash
docker compose exec postgres psql -U hypothesis -d hypothesis_factory
```

Внутри `psql`:

```sql
\dt
SELECT * FROM corpus_run_summary ORDER BY created_at DESC LIMIT 10;
SELECT COUNT(*) FROM source_files;
SELECT COUNT(*) FROM document_texts;
SELECT COUNT(*) FROM document_chunks;
SELECT COUNT(*) FROM kg_entities;
SELECT COUNT(*) FROM kg_relations;
```

Первые строки `document_texts`:

```sql
SELECT run_id, source_file_id, title, LEFT(text, 500) AS text_preview
FROM document_texts
LIMIT 10;
```

## Если Exporter Остановился

Смотри manifest на диске:

```bash
tail -n 40 /media/andy/XS2000/data_hack/oqmd/formationenergy_full/manifest.json
wc -l /media/andy/XS2000/data_hack/oqmd/formationenergy_full/oqmd_formationenergy.jsonl
```

Большие exporters обычно поддерживают `--resume`. Для OQMD при timeout:

```bash
docker compose run --rm hypothesis-factory python scripts/export_oqmd.py \
  --output-dir /data_hack/oqmd/formationenergy_full \
  --all \
  --yes-all \
  --limit 0 \
  --page-size 100 \
  --resume \
  --sleep 0.5 \
  --timeout 120 \
  --continue-on-error \
  --min-page-size 1 \
  --max-consecutive-skips 50 \
  --ingest-db \
  --run-name oqmd-formationenergy-full-recovery
```

## Если `latest` Не Тот Run

`latest` выбирается по `ingest_runs.created_at`. Если параллельно идет OQMD/OpenAlex/PDF scraper, `latest` может указывать не на тот corpus.

Список:

```sql
SELECT id, name, status, created_at
FROM ingest_runs
ORDER BY created_at DESC
LIMIT 20;
```

Запускай KG по конкретному id:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id <run_id> \
  --stages sections,assets,entities,relations,embeddings,sync
```

## Если Neo4j/Qdrant/GROBID Недоступны

Ожидаемое поведение:

- `kg_worker` не должен портить Postgres;
- `kg_sync_status` получает `skipped` или `failed`;
- search fallback из Postgres все равно работает.

Проверить sync:

```sql
SELECT target, status, counts, error, updated_at
FROM kg_sync_status
ORDER BY updated_at DESC;
```

Перезапустить сервисы:

```bash
docker compose up -d neo4j qdrant grobid
```

Повторить только sync:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id <run_id> \
  --stages sync
```

## Если Docker Пишет Orphan Containers

Удалить старые одноразовые containers:

```bash
docker compose down --remove-orphans
```

Не добавляй `-v`, если не хочешь удалить volumes с Postgres/Neo4j/Qdrant.

## Если Нужно Полностью Пересобрать Индексы

Neo4j/Qdrant являются derived indexes. Можно пересобрать:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id <run_id> \
  --stages embeddings,sync
```

Если нужно пересоздать весь KG:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id <run_id> \
  --stages sections,assets,entities,relations,embeddings,sync
```

## Smoke Test После Изменений

```bash
cd hypothesis-factory
python -m unittest discover -s tests

cd /home/andy/cursorprj/hackNOR
docker compose config
docker compose run --rm hypothesis-factory python -m backend.kg_worker status --run-id latest
```
