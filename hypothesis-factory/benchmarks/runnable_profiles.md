# Runnable profile benchmark

- Documents: `1907`
- Chunks: `17067`
- Source: `PostgreSQL corpus`

| Rank | ID | Profile | Retrieval | Quality | Hypotheses | Breadth | Avg score | Evidence | Warnings | Runtime, s |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | v17 | Metadata-filtered Vector RAG | auto | 0.649 | 6 | 0.333 | 0.785 | 1.000 | 0.000 | 3.1053 |
| 2 | v16 | Vector KG RAG | kg | 0.648 | 6 | 0.333 | 0.783 | 1.000 | 0.000 | 3.1179 |
| 3 | v18 | Full KG Vector Factory | auto | 0.627 | 3 | 0.167 | 0.768 | 1.000 | 0.000 | 0.2724 |
| 4 | v02 | Keyword RAG only | tfidf | 0.513 | 6 | 1.000 | 0.642 | 0.333 | 0.667 | 0.6443 |
| 5 | v14 | Computer vision froth control | tfidf | 0.513 | 6 | 1.000 | 0.642 | 0.333 | 0.667 | 0.6895 |
| 6 | v03 | Dense RAG only | tfidf | 0.498 | 6 | 0.857 | 0.642 | 0.333 | 0.667 | 0.6858 |
| 7 | v04 | Hybrid RAG | tfidf | 0.495 | 6 | 0.750 | 0.666 | 0.333 | 0.667 | 0.6837 |
| 8 | v07 | RAG + KG + transparent scoring | tfidf | 0.470 | 6 | 0.500 | 0.666 | 0.333 | 0.667 | 0.6096 |
| 9 | v05 | RAG + numeric Excel priority | tfidf | 0.463 | 5 | 0.500 | 0.655 | 0.400 | 0.600 | 0.6456 |
| 10 | v09 | Multi-agent RAG | tfidf | 0.444 | 3 | 0.300 | 0.646 | 0.333 | 0.667 | 0.6365 |
| 11 | v01 | Single LLM chatbot | tfidf | 0.431 | 5 | 1.000 | 0.704 | 0.000 | 1.000 | 0.6340 |
| 12 | v15 | Recommended hybrid factory | tfidf | 0.431 | 3 | 0.167 | 0.646 | 0.333 | 0.667 | 0.6790 |
| 13 | v08 | Multi-agent only | tfidf | 0.424 | 3 | 0.375 | 0.623 | 0.333 | 1.000 | 0.6429 |
| 14 | v11 | Physics + process heuristics | tfidf | 0.348 | 1 | 0.100 | 0.728 | 0.000 | 1.000 | 0.6243 |
| 15 | v13 | Digital twin first | tfidf | 0.348 | 1 | 0.125 | 0.718 | 0.000 | 1.000 | 0.6823 |
| 16 | v06 | RAG + Knowledge Graph | tfidf | 0.345 | 1 | 0.100 | 0.718 | 0.000 | 1.000 | 0.6678 |
| 17 | v10 | Multi-agent RAG + KG | tfidf | 0.344 | 1 | 0.083 | 0.718 | 0.000 | 1.000 | 0.6209 |
| 18 | v12 | Predictive ML only | tfidf | 0.000 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.6383 |

## Top examples

### v17 Metadata-filtered Vector RAG
- 1. `0.830` Автоматизация подачи воды в мельницы (expert_seed, evidence=4, warnings=0)
- 2. `0.829` Перераспределение фронта контрольной флотации (expert_seed, evidence=4, warnings=0)
- 3. `0.827` Настройка песковых насадок гидроциклонов (expert_seed, evidence=4, warnings=0)

### v16 Vector KG RAG
- 1. `0.828` Автоматизация подачи воды в мельницы (expert_seed, evidence=4, warnings=0)
- 2. `0.828` Перераспределение фронта контрольной флотации (expert_seed, evidence=4, warnings=0)
- 3. `0.826` Настройка песковых насадок гидроциклонов (expert_seed, evidence=4, warnings=0)

### v18 Full KG Vector Factory
- 1. `0.837` Настройка песковых насадок гидроциклонов (expert_seed, evidence=4, warnings=0)
- 2. `0.827` A/B-карта плотности пульпы и времени агитации (counterfactual, evidence=4, warnings=0)
- 3. `0.642` Тонкое грохочение после второй стадии измельчения (expert_seed, evidence=4, warnings=0)

### v02 Keyword RAG only
- 1. `0.732` Перераспределение фронта контрольной флотации (expert_seed, evidence=0, warnings=1)
- 2. `0.732` Автоматизация подачи воды в мельницы (expert_seed, evidence=0, warnings=1)
- 3. `0.718` A/B-карта плотности пульпы и времени агитации (counterfactual, evidence=0, warnings=1)

### v14 Computer vision froth control
- 1. `0.732` Перераспределение фронта контрольной флотации (expert_seed, evidence=0, warnings=1)
- 2. `0.732` Автоматизация подачи воды в мельницы (expert_seed, evidence=0, warnings=1)
- 3. `0.718` A/B-карта плотности пульпы и времени агитации (counterfactual, evidence=0, warnings=1)
