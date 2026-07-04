# Источники Данных

Система поддерживает локальные файлы и несколько внешних материаловедческих источников. Все источники приводятся к одному corpus shape: `source_files`, `document_texts`, `document_chunks`, `structured_records`.

## Локальная Папка `Задача 1`

Docker mount:

```text
./Задача 1 -> /workspace/Задача 1:ro
```

Запуск:

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker ingest \
  --path /workspace/Задача\ 1 \
  --run-name zadacha1
```

Поддерживаются:

- `.docx`
- `.xlsx`
- `.pdf`
- `.txt`, `.md`
- `.png`, `.jpg`, `.jpeg`

## Materials Project

Назначение: reference/benchmark база DFT и derived properties.

Что сохраняется:

- `source_type=materials_project`
- `document_texts`: technical summary.
- `structured_records.record_type=materials_project_summary`
- chunks для retrieval.

Нужен API key:

```bash
MP_API_KEY=...
```

Small query:

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker materials-project \
  --chemsys Fe-Cr-Ni \
  --fields material_id,formula_pretty,band_gap,energy_above_hull,formation_energy_per_atom,symmetry,structure \
  --limit 100 \
  --run-name mp-fe-cr-ni
```

Большой resumable export:

```bash
docker compose run --rm hypothesis-factory python scripts/export_materials_project.py \
  --output-dir /data_hack/materials_project/full_summary \
  --all \
  --yes-all \
  --limit 0 \
  --chunk-size 1000 \
  --resume \
  --ingest-db \
  --run-name mp-all-summary-full
```

Важно: это не “скрапинг всего интернета”. Это выгрузка summary records из Materials Project API.

## OQMD

Назначение: DFT formation energy, stability, structures.

Источник:

```text
https://oqmd.org/oqmdapi/formationenergy
```

API key не нужен.

Smoke:

```bash
docker compose run --rm hypothesis-factory python scripts/export_oqmd.py \
  --output-dir /data_hack/oqmd/smoke \
  --all \
  --limit 100 \
  --page-size 50 \
  --resume \
  --ingest-db \
  --run-name oqmd-smoke
```

Полный прогон с recovery mode:

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

Почему recovery нужен: OQMD API может timeout на отдельных offsets. Exporter умеет пропускать проблемные offsets и продолжать, чтобы manifest/run не портились.

## OpenAlex + Unpaywall

Назначение: metadata, abstracts, citations, topics, DOI, OA links.

OpenAlex profiles:

- `core`: materials science, solid state chemistry, DFT/materials.
- `adjacent`: physical chemistry, chemical physics, interfaces, diffusion.
- `mining`: metallurgy, hydrometallurgy, flotation, corrosion.
- `energy`: batteries, fuel cells, electrocatalysis, photovoltaics.
- `bio-soft`: biomaterials, polymers, hydrogels, colloids.
- `computational`: materials informatics, MD, CALPHAD, phase-field.
- `full`: объединение всех профилей.

Посмотреть профили:

```bash
docker compose run --rm hypothesis-factory python scripts/export_literature_openalex.py --list-profiles
```

Seed:

```bash
docker compose run --rm hypothesis-factory python scripts/export_literature_openalex.py \
  --output-dir /data_hack/literature/openalex_adjacent_seed \
  --profile adjacent \
  --yes-large \
  --limit-per-query 1000 \
  --per-page 200 \
  --resume \
  --unpaywall auto \
  --ingest-db \
  --run-name openalex-adjacent-seed
```

Широкий сбор:

```bash
docker compose run --rm hypothesis-factory python scripts/export_literature_openalex.py \
  --output-dir /data_hack/literature/openalex_full \
  --profile full \
  --yes-large \
  --limit-per-query 5000 \
  --per-page 200 \
  --resume \
  --unpaywall auto \
  --ingest-db \
  --run-name openalex-full
```

Unpaywall требует email:

```text
UNPAYWALL_EMAIL=
OPENALEX_MAILTO=
```

## PAN Journals PDF Scraper

Назначение: скачать легально доступные PDF из journals.pan.pl по металлургии и смежным областям.

Пример:

```bash
python hypothesis-factory/scripts/scrape_pan_journals_pdfs.py \
  --output-dir /media/andy/XS2000/data_hack/pdf_arch \
  --default-queries \
  --source sitemap \
  --max-pdfs 0 \
  --yes-large \
  --resume \
  --sleep 1.0
```

После скачивания PDF кладутся на диск. Затем их нужно ingest:

```bash
docker compose run --rm hypothesis-factory python -m backend.corpus_worker ingest \
  --path /data_hack/pdf_arch \
  --run-name pan-pdf-arch \
  --ocr auto \
  --deepseek auto
```

## Что Не Делаем

Sci-Hub не интегрирован. Pipeline не скачивает copyrighted PDF из пиратских зеркал. Для full text используем legal/open-access источники: Unpaywall OA links, publisher OA, arXiv, PMC, CORE, DOAJ и открытые PDF archives.
