# Iterative benchmark optimization: 18 variants x 100 iterations

Это воспроизводимый архитектурный benchmark-improve loop. Он не утверждает, что все 18 production pipeline полностью реализованы; он фиксирует, какие улучшения дают наибольший marginal gain по выбранным метрикам и как меняется рейтинг после заданных шагов улучшения каждого варианта.

- Iterations per variant: `100`
- Total benchmark/improvement iterations: `1800`

| Rank | ID | Variant | Initial | Final | Gain |
|---:|---|---|---:|---:|---:|
| 1 | v18 | Full KG Vector Factory | 0.823 | 0.875 | 0.052 |
| 2 | v15 | Recommended hybrid factory | 0.808 | 0.866 | 0.058 |
| 3 | v17 | Metadata-filtered Vector RAG | 0.796 | 0.856 | 0.061 |
| 4 | v16 | Vector KG RAG | 0.780 | 0.849 | 0.069 |
| 5 | v07 | RAG + KG + transparent scoring | 0.757 | 0.839 | 0.082 |
| 6 | v11 | Physics + process heuristics | 0.684 | 0.825 | 0.141 |
| 7 | v10 | Multi-agent RAG + KG | 0.690 | 0.812 | 0.122 |
| 8 | v06 | RAG + Knowledge Graph | 0.697 | 0.812 | 0.115 |
| 9 | v05 | RAG + numeric Excel priority | 0.660 | 0.802 | 0.143 |
| 10 | v13 | Digital twin first | 0.617 | 0.786 | 0.169 |
| 11 | v09 | Multi-agent RAG | 0.628 | 0.783 | 0.156 |
| 12 | v04 | Hybrid RAG | 0.534 | 0.752 | 0.218 |
| 13 | v14 | Computer vision froth control | 0.523 | 0.732 | 0.209 |
| 14 | v02 | Keyword RAG only | 0.426 | 0.717 | 0.291 |
| 15 | v08 | Multi-agent only | 0.448 | 0.712 | 0.264 |
| 16 | v03 | Dense RAG only | 0.400 | 0.700 | 0.300 |
| 17 | v12 | Predictive ML only | 0.377 | 0.687 | 0.310 |
| 18 | v01 | Single LLM chatbot | 0.217 | 0.639 | 0.422 |

Best final pipeline: **v18 Full KG Vector Factory**.

Практический вывод: сильнее всего растут варианты, где LLM/agents не заменяют доказательную базу, а работают поверх corpus DB, Excel priority, KG, constraints, OCR/provenance и expert feedback.

Файлы:

- `benchmarks/iterative_optimization.csv`: все 1800 строк benchmark before/after.
- `benchmarks/iterative_optimization_summary.json`: машинно-читаемая финальная сводка.