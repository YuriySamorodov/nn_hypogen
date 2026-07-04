# PDF, OCR, GROBID И Таблицы/Формулы

PDF pipeline устроен как каскад. Цель: получить полный текст, секции, таблицы, формулы и provenance без молчаливой пустоты.

## Каскад Обработки

```text
PDF
  -> Poppler pdftotext -layout
  -> heuristic tables/formulas
  -> OCR fallback if text is weak
  -> GROBID TEI during KG build
  -> DeepSeek optional cleanup for bad OCR
  -> Postgres document_texts/document_sections/document_assets
```

## Poppler Extraction

Файл:

```text
backend/services/pdf_converter.py
```

Что делает:

- запускает `pdftotext -layout`;
- сохраняет layout-preserving text;
- извлекает формулы/уравнения эвристиками;
- извлекает table-like blocks по колонкам;
- записывает records в `structured_records`:
  - `pdf_formulas`
  - `pdf_tables`

Если текста мало, ставится `ocr_required=true`.

## OCR Fallback

Файл:

```text
backend/services/pdf_ocr.py
```

Используется для:

- scan PDF;
- PNG/JPG;
- PDF, где Poppler дал слишком мало текста.

Настройки:

```text
PDF_OCR_MAX_PAGES=12
PDF_OCR_DPI=200
PDF_OCR_LANGUAGES=rus+eng
PDF_OCR_MIN_CHARS=800
PDF_OCR_QUALITY_THRESHOLD=0.35
```

Если OCR не дал хорошего результата, создается artifact со статусом `failed`/`skipped`, а run становится degraded, но ingestion не падает полностью.

## DeepSeek OCR Cleanup

DeepSeek используется только как optional fallback:

- нет API key: `llm_calls.status=skipped`;
- есть API key: модель получает локальный OCR text и просит вернуть JSON с cleaned text/tables/formulas/quality notes;
- DeepSeek не считается источником истины и не должен выдумывать missing content.

Настройки:

```text
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL_STRUCT=deepseek-v4-pro
DEEPSEEK_MODEL_FAST=deepseek-v4-flash
```

## GROBID TEI

GROBID запускается не в обычном corpus ingestion, а на стадии KG build:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id latest \
  --stages sections,assets \
  --grobid auto
```

Что извлекает:

- title;
- DOI;
- authors;
- abstract;
- body sections;
- references;
- figures/tables/formulas when present in TEI.

Raw TEI сохраняется как artifact:

```text
artifacts.kind=grobid_tei
artifacts.stage=kg_sections
```

Секции идут в:

```text
document_sections
```

Таблицы/картинки/формулы идут в:

```text
document_assets
```

## Fallback Без GROBID

Если GROBID недоступен:

- `kg_worker` создает sections из `document_texts.text`;
- assets собираются из `structured_records` (`pdf_tables`, `pdf_formulas`);
- `kg_sync_status` и artifacts покажут degraded/skipped статус;
- Postgres corpus остается пригодным.

## Практический Запуск Для Архива PDF

1. Скачать PDF в `/data_hack/pdf_arch`.

2. Ingest:

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker ingest \
  --path /data_hack/pdf_arch \
  --run-name pan-pdf-arch \
  --ocr auto \
  --deepseek auto
```

3. KG sections/assets:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker build \
  --run-id latest \
  --stages sections,assets \
  --grobid auto
```

4. Проверить:

```bash
docker compose run --rm hypothesis-factory python -m backend.kg_worker status --run-id latest
```
