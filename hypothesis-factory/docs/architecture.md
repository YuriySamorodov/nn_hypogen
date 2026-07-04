# Архитектура Hypothesis Factory

```text
Files/KPI/constraints
  -> ingestion
  -> chunks + structured Excel records
  -> entity/relation extraction
  -> hybrid retrieval
  -> Process-Particle-Property-KPI knowledge graph
  -> hypothesis generators
  -> agentic review
  -> validation
  -> transparent scoring
  -> expert feedback
  -> JSON/CSV/PDF export
```

## Почему гибрид

Один LLM не знает локальный баланс хвостов, не видит таблицы крупности и не может доказать, почему гипотеза важнее другой. Поэтому MVP использует:

- Excel parser для тоннажа потерь и извлекаемости.
- Hybrid retrieval для цитируемых evidence-фрагментов.
- Knowledge graph для причинной цепочки.
- Rule-based generators для стабильного demo без API.
- Lightweight agentic review: reflection, proximity/diversity, evolution.
- Optional LLM layer для production-качества формулировок.
- Weighted scoring для прозрачного ранжирования.

## Почему это интерпретируемо

Каждая гипотеза хранит:

- target KPI;
- целевой поток/класс/элемент;
- proposed change;
- causal chain;
- evidence list;
- risks;
- validation plan;
- score breakdown.

## Production roadmap

1. OCR/Docling/olmOCR для PDF и PNG.
2. Исторические временные ряды фабрики.
3. Froth computer vision.
4. Интеграция historian/DCS.
5. LLM agents: генератор, критик, ранжировщик, meta-review.
6. Проверка novelty через Semantic Scholar, patents, MatKG.
7. Closed-loop optimization после пилотов.
