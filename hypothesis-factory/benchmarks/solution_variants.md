# Benchmark: 18 pipeline solution variants

| Rank | ID | Variant | Score | Why |
|---:|---|---|---:|---|
| 1 | v18 | Full KG Vector Factory | 0.823 | Vector KG retrieval + metadata filters + agentic review + transparent scoring. |
| 2 | v15 | Recommended hybrid factory | 0.808 | RAG + KG + numeric priority + multi-agent critique + expert feedback + optional LLM. |
| 3 | v17 | Metadata-filtered Vector RAG | 0.796 | Vector retrieval plus plant/element/size metadata filters. |
| 4 | v16 | Vector KG RAG | 0.780 | KG embeddings ledger with vector evidence retrieval. |
| 5 | v07 | RAG + KG + transparent scoring | 0.757 | Граф, evidence и прозрачная weighted scoring формула. |
| 6 | v06 | RAG + Knowledge Graph | 0.697 | Evidence chunks и PSP/KPI-граф без предиктивной модели. |
| 7 | v10 | Multi-agent RAG + KG | 0.690 | Agents, RAG, KG, но без явного numeric priority. |
| 8 | v11 | Physics + process heuristics | 0.684 | Правила флотации/крупности без LLM. |
| 9 | v05 | RAG + numeric Excel priority | 0.660 | RAG плюс ранжирование по тоннажу потерь из Excel. |
| 10 | v09 | Multi-agent RAG | 0.628 | Co-Scientist style debate поверх RAG. |
| 11 | v13 | Digital twin first | 0.617 | Полный цифровой двойник фабрики. |
| 12 | v04 | Hybrid RAG | 0.534 | BM25 + dense retrieval + JSON hypothesis prompt. |
| 13 | v14 | Computer vision froth control | 0.523 | CV по пене и online setpoints. |
| 14 | v08 | Multi-agent only | 0.448 | Генератор, критик и ранжировщик без структурных данных. |
| 15 | v02 | Keyword RAG only | 0.426 | Поиск по документам и генерация ответа без графа и scoring. |
| 16 | v03 | Dense RAG only | 0.400 | Embedding retrieval без учета точных классов крупности. |
| 17 | v12 | Predictive ML only | 0.377 | Модель извлечения по историческим данным без объяснения. |
| 18 | v01 | Single LLM chatbot | 0.217 | Один prompt без RAG и числовых проверок. |

Best pipeline: **Full KG Vector Factory**.