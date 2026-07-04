# Docker Stack

Docker stack описан в корневом `docker-compose.yml`.

## Сервисы

```text
postgres
neo4j
qdrant
grobid
hypothesis-factory
```

## Запуск

Минимум для corpus ingestion:

```bash
docker compose up -d postgres hypothesis-factory
```

Полный stack:

```bash
docker compose up -d postgres neo4j qdrant grobid hypothesis-factory
```

Пересборка Python image:

```bash
docker compose build hypothesis-factory
```

## Ports

Host ports:

```text
Postgres: 55432 -> 5432
Neo4j browser: 57474 -> 7474
Neo4j bolt: 57687 -> 7687
Qdrant HTTP: 56333 -> 6333
Qdrant gRPC: 56334 -> 6334
GROBID: 58070 -> 8070
```

## Volumes

Persistent Docker volumes:

```text
hacknor_postgres_data
hacknor_neo4j_data
hacknor_neo4j_logs
hacknor_qdrant_data
```

Host mounts:

```text
./Задача 1 -> /workspace/Задача 1:ro
./hypothesis-factory/data -> /app/data
/media/andy/XS2000/data_hack -> /data_hack
```

## Environment

`hypothesis-factory` получает внутри Docker:

```text
CORPUS_DATABASE_URL=postgresql://hypothesis:hypothesis@postgres:5432/hypothesis_factory
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=hypothesiskg
QDRANT_URL=http://qdrant:6333
GROBID_URL=http://grobid:8070
```

Дополнительные ключи кладутся в:

```text
hypothesis-factory/.env
```

Пример:

```text
MP_API_KEY=
OPENALEX_MAILTO=
UNPAYWALL_EMAIL=
DEEPSEEK_API_KEY=
```

## Проверки

Compose syntax:

```bash
docker compose config
```

Postgres:

```bash
docker compose ps postgres
docker compose exec postgres pg_isready -U hypothesis -d hypothesis_factory
```

Corpus status:

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker status --run-id latest
```

KG status:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker status --run-id latest
```

## Orphan Containers

Если Docker пишет:

```text
Found orphan containers
```

это старые одноразовые `docker compose run` контейнеры. Удалить:

```bash
docker compose down --remove-orphans
```

Это не удаляет named volumes, если не добавлять `-v`.
