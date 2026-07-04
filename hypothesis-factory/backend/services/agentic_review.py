from __future__ import annotations

from collections import defaultdict

from backend.schemas import Hypothesis, KnowledgeBase


def run_agentic_review(hypotheses: list[Hypothesis], kb: KnowledgeBase) -> list[Hypothesis]:
    """Lightweight local analogue of modern co-scientist loops.

    Generation is handled upstream. This layer adds three deterministic agents:
    reflection, proximity/diversity, and evolution. It keeps demo behavior
    reproducible while mirroring the 2025-2026 multi-agent pattern.
    """
    reflected = [_reflection_agent(hyp) for hyp in hypotheses]
    diversified = _proximity_agent(reflected)
    evolved = diversified + _evolution_agent(diversified, kb)
    return evolved


def _reflection_agent(hypothesis: Hypothesis) -> Hypothesis:
    if len(hypothesis.evidence) < 2:
        hypothesis.warnings.append("Reflection agent: weak evidence pack, needs stronger source support.")
    if not hypothesis.target_size_class and hypothesis.generator not in {"analogy", "prediction_guided", "expert_seed"}:
        hypothesis.warnings.append("Reflection agent: no target size class.")
    if any(word in hypothesis.proposed_change.lower() for word in ["замена", "добавление"]):
        hypothesis.risks.append("Reflection agent: проверить CapEx, сроки монтажа и влияние на простои.")
    return hypothesis


def _proximity_agent(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    buckets: dict[tuple[str | None, str | None, str], list[Hypothesis]] = defaultdict(list)
    for hyp in hypotheses:
        buckets[(hyp.target_plant, hyp.target_size_class, hyp.generator)].append(hyp)
    selected: list[Hypothesis] = []
    for group in buckets.values():
        selected.extend(group[:2])
    return selected


def _evolution_agent(hypotheses: list[Hypothesis], kb: KnowledgeBase) -> list[Hypothesis]:
    if not hypotheses:
        return []
    plants_with_coarse_loss = {
        rec.plant
        for rec in kb.size_classes
        if (rec.size_class.startswith("+") or "125" in rec.size_class)
        and max(rec.element28_tonnes or 0, rec.element29_tonnes or 0) > 500
    }
    evolved: list[Hypothesis] = []
    for plant in sorted(plants_with_coarse_loss)[:2]:
        base = next((hyp for hyp in hypotheses if hyp.target_plant == plant), None)
        if base is None:
            continue
        new = base.model_copy(deep=True)
        new.id = f"{base.id}e"
        new.title = f"{plant}: combined coarse-class control and hydrocyclone tuning"
        new.proposed_change = (
            "Объединить контроль крупного класса, настройку песковых насадок гидроциклонов "
            "и короткий цикл доизмельчения для снижения закрытых сростков Pnt/Cp."
        )
        new.hypothesis_text = f"{new.proposed_change} Гипотеза создана evolution agent из сильных локальных кандидатов."
        new.generator = "evolution_agent"
        new.novelty_rationale = "Комбинация объединяет числовой приоритет хвостов и экспертные гипотезы по гидроциклонам."
        evolved.append(new)
    return evolved

