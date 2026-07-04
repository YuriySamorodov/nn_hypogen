# Scoring methodology

Финальная оценка:

```text
FinalScore =
  w_kpi_impact * KPIImpact
+ w_feasibility * Feasibility
+ w_evidence * EvidenceStrength
+ w_causal * CausalConsistency
+ w_novelty * Novelty
+ w_business * BusinessValue
+ w_implementability * Implementability
- w_risk * Risk
```

По умолчанию:

```text
kpi_impact: 0.22
feasibility: 0.15
evidence_strength: 0.15
causal_consistency: 0.15
novelty: 0.08
business_value: 0.10
implementability: 0.10
risk: 0.15
```

Для данной задачи `KPIImpact` считается прежде всего через тоннаж потенциально извлекаемых потерь в целевом классе/потоке. Это намеренно: гипотеза, которая улучшает маленький класс, не должна обгонять гипотезу по крупному источнику потерь без сильного дополнительного обоснования.

