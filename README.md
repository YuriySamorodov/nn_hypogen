# Фабрика гипотез: учебный RAG-прототип

Локальный прототип на `Chainlit + LangChain + Qdrant + DeepSeek API`.
Система индексирует демо-корпус и (опционально) материалы из `Задача 1`, затем
генерирует структурированные, проверяемые гипотезы с источниками, механизмом
влияния, рисками, ожидаемым KPI и планом валидации. Для снижения галлюцинаций
используется два контура:

- Qdrant retrieval по PDF/DOCX/XLSX и OCR-тексту PNG-схем;
- локальный knowledge graph из Excel-файлов `Пример 1`, `Пример 2`, ... с
  фактами по хвостам, классам крупности, формам потерь и извлекаемому металлу.

После генерации выполняется проверка привязки источников (grounding): цитаты
без совпадения с retrieved context помечаются как не подтверждённые.

## Быстрый старт (локально)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Заполните `DEEPSEEK_API_KEY` в `.env`, затем поднимите Qdrant и проиндексируйте
корпус:

```bash
docker compose up -d qdrant
python -m src.ingest --recreate
DEBUG=false chainlit run app.py
```

Откройте URL, который напечатает Chainlit, и попробуйте запрос:

```text
Цель: повысить жаропрочность никелевого сплава на 15%.
Ограничения: ниобий не выше 0.3%, без вакуумной плавки, бюджет на проверку 2 недели.
Количество гипотез: 3
```

## Запуск одной командой (Docker)

```bash
cp .env.example .env   # укажите DEEPSEEK_API_KEY
docker compose up --build
```

Приложение будет доступно на `http://localhost:8000`. Контейнер `app` дождётся
Qdrant, выполнит ingest и запустит Chainlit.

## OCR для PNG-схем

Для распознавания текста на схемах флотации и регламентах нужен Tesseract:

- **Docker:** установлен автоматически в образе `app`.
- **Windows/Linux локально:** установите [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)
  с языковыми пакетами `rus` и `eng`. Без Tesseract PNG индексируются как метаданные.

Отключить OCR: `ENABLE_OCR=false` в `.env`.

## Материалы `Задача 1` (не в Git)

Папка `Задача 1/` **не включена в репозиторий** — PDF, Excel и схемы слишком
большие для push без Git LFS storage.

**Для полного корпуса** скопируйте папку из архива конкурса локально:

```text
artem_huck-main/
  Задача 1/
    Пример 1/
    Пример 2/
    ...
```

Затем переиндексируйте: `python -m src.ingest --recreate`.

Без `Задача 1` проект работает на демо-корпусе (`data/demo_corpus/`) и
предсобранном графе знаний (`data/knowledge_graph/graph.json`).

## Экспорт

После генерации гипотез в чате появляются кнопки скачивания **Markdown** и **DOCX**.
Команда `/export` повторяет выгрузку последнего результата.
Файлы также сохраняются в каталог `exports/`.

## Что входит в MVP

- демо-корпус `data/demo_corpus/` (4 markdown-источника);
- предсобранный `data/knowledge_graph/graph.json` (~927 фактов);
- опционально материалы из `Задача 1`: PDF, DOCX, XLSX, OCR для PNG;
- индексация документов в Qdrant;
- построение `data/knowledge_graph/graph.json` из Excel-файлов;
- graph retrieval поверх структурированных Excel-фактов;
- retrieval с метаданными источников;
- генерация гипотез DeepSeek через OpenAI-compatible API;
- Pydantic-валидация JSON-ответа;
- grounding check для evidence;
- ранжирование по новизне, реализуемости и риску;
- Chainlit-интерфейс и экспорт MD/DOCX;
- Docker Compose для развёртывания жюри.

## Что намеренно вне MVP

- загрузка пользовательских PDF/Excel через UI;
- роли, авторизация, шифрование;
- Jira/YouTrack-интеграции;
- PDF-отчёты;
- обучение на экспертном feedback loop.

## Тесты

```bash
pytest
```

Тесты не вызывают DeepSeek API и не требуют запущенного Qdrant-контейнера.
