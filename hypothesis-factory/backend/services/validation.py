from __future__ import annotations

from backend.schemas import Constraints, Hypothesis


def validate_hypothesis(hypothesis: Hypothesis, constraints: Constraints) -> Hypothesis:
    text = " ".join([hypothesis.title, hypothesis.hypothesis_text, hypothesis.proposed_change]).lower()
    warnings: list[str] = list(hypothesis.warnings)
    for element in constraints.forbidden_elements:
        if element.lower() in text:
            warnings.append(f"Запрещенный элемент в гипотезе: {element}")
    for equipment in constraints.unavailable_equipment:
        if equipment.lower() in text:
            warnings.append(f"Недоступное оборудование: {equipment}")
    if constraints.no_capex and any(word in text for word in ["замена", "новый", "добавление", "строительство"]):
        warnings.append("Ограничение no_capex: гипотеза требует капитального изменения или нового узла.")
    if not hypothesis.evidence:
        warnings.append("Нет evidence-фрагментов.")
    if len(hypothesis.causal_chain) < 3:
        warnings.append("Недостаточно полная причинная цепочка.")
    if not hypothesis.validation_plan:
        warnings.append("Нет плана проверки.")
    hypothesis.warnings = warnings
    return hypothesis


def validate_hypotheses(hypotheses: list[Hypothesis], constraints: Constraints) -> list[Hypothesis]:
    return [validate_hypothesis(hyp, constraints) for hyp in hypotheses]

