# 15 вариантов решения и выбор лучшего пайплайна

Критерии сравнения:

- `Interpretability`: можно ли объяснить гипотезу эксперту.
- `Evidence grounding`: опирается ли гипотеза на локальные файлы, таблицы, PDF и схемы.
- `Domain fit`: подходит ли решение именно для хвостов флотации Ni/Cu.
- `Modernity 2026`: учитывает ли agentic AI, RAG, KG, цифровые двойники, feedback loop.
- `Local runnability`: запустится ли на ноутбуке без GPU и внешних сервисов.
- `Implementation risk`: риск не успеть или получить нестабильный demo.

## Варианты

1. Single LLM chatbot: быстро, но высокий риск галлюцинаций, слабая проверяемость.
2. Keyword RAG only: лучше чатбота, но плохо ловит причинные связи.
3. Dense RAG only: хорошо ищет смысл, но теряет точные классы `+125`, `-10`, `Pnt/Cp`.
4. Hybrid RAG: BM25/keyword + embeddings, нормальная база для evidence.
5. RAG + numeric Excel priority: уже полезно для задачи, потому что Excel задает экономический приоритет.
6. RAG + Knowledge Graph: добавляет PSP/KPI-цепочки и интерпретируемость.
7. RAG + KG + transparent scoring: сильная локальная MVP-архитектура.
8. Multi-agent only: современно, но без структурных данных ненадежно.
9. Multi-agent RAG: хороший research assistant, но не хватает числовой фабричной логики.
10. Multi-agent RAG + KG: сильный вариант, но сложнее стабилизировать в демо.
11. Physics + process heuristics: надежно и локально, но хуже генерирует нестандартные идеи.
12. Predictive ML only: без historian-данных будет слишком слабым.
13. Digital twin first: правильно для production, но тяжелый путь для MVP.
14. Computer vision froth control: перспективно для online control, но не решает текущую задачу на имеющихся файлах.
15. Recommended hybrid factory: RAG + KG + numeric priority + multi-agent critique + expert feedback + optional LLM.

## Лучший выбор

Лучший пайплайн для этой папки и hackathon MVP: **Recommended hybrid factory**.

Причина: локальные данные уже содержат quantitative evidence в Excel, process constraints в схемах/регламентах, expert seeds в DOCX и теоретическую базу в PDF. Поэтому MVP должен сначала структурировать эти данные, затем строить evidence-backed гипотезы, а LLM использовать как генератор/критик, а не как единственный источник истины.

Команда для пересчета benchmark:

```bash
python scripts/benchmark_pipeline_variants.py
python scripts/benchmark_iterative_optimization.py 100
python scripts/benchmark_runnable_profiles.py ../Задача\ 1
```

## 100-итерационный optimization benchmark

Дополнительно добавлен воспроизводимый цикл `benchmark -> improvement -> benchmark`
для каждого из 15 вариантов. Он делает 100 итераций на вариант, всего 1500 строк
измерений в `benchmarks/iterative_optimization.csv`.

Итог после 100 итераций:

1. `v15 Recommended hybrid factory`: `0.808 -> 0.866`
2. `v07 RAG + KG + transparent scoring`: `0.757 -> 0.839`
3. `v11 Physics + process heuristics`: `0.684 -> 0.825`

Практический вывод не изменился: лучший путь для текущей задачи - гибридный
pipeline, где LLM/agents работают поверх corpus DB, Excel priority, KG,
constraints, OCR/provenance и expert feedback, а не заменяют evidence/scoring.

## Runnable profile benchmark

Помимо архитектурной оценки, добавлены 15 исполняемых pipeline profiles. Каждый
profile запускается на одном `KnowledgeBase`, генерирует гипотезы, проходит
validation/scoring и получает quality/runtime метрики.

Последний локальный результат:

1. `v15 Recommended hybrid factory`: quality `0.806`, 11 гипотез.
2. `v07 RAG + KG + transparent scoring`: quality `0.797`, 12 гипотез.
3. `v10 Multi-agent RAG + KG`: quality `0.781`, 8 гипотез.

Тот же benchmark проверен против PostgreSQL corpus через Docker:

```bash
docker compose run --rm hypothesis-factory python scripts/benchmark_runnable_profiles.py "/workspace/Задача 1" --from-db --run-id latest
```

Результаты сохраняются в `benchmarks/runnable_profiles.*`.
