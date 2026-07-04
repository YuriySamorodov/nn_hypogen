# 5-minute demo script

1. Открыть приложение:

```bash
streamlit run app/streamlit_app.py
```

2. Показать KPI: `Снизить потери Ni/Cu в отвальных хвостах на 5%`.
3. Запустить генерацию на папке `../Задача 1`.
4. Показать таблицу ranked hypotheses.
5. Открыть top-1 гипотезу:
   - evidence;
   - causal chain;
   - score breakdown;
   - risks;
   - validation plan.
6. Изменить вес `risk` или `feasibility`, показать изменение ранжирования.
7. Показать exports в `data/demo_outputs/`.
8. Показать benchmark 15 вариантов в `benchmarks/solution_variants.md`.
9. Показать runtime benchmark:

```bash
python scripts/benchmark_runtime.py ../Задача\ 1
```
