from __future__ import annotations

import hashlib

from backend.schemas import Evidence, Hypothesis, KnowledgeBase, PipelineInput, ValidationStep
from backend.services.retrieval import HybridRetriever


def generate_hypotheses(kb: KnowledgeBase, pipeline_input: PipelineInput, retriever: HybridRetriever) -> list[Hypothesis]:
    hypotheses: list[Hypothesis] = []
    hypotheses.extend(_numeric_priority_hypotheses(kb, pipeline_input, retriever))
    hypotheses.extend(_expert_seed_hypotheses(kb, pipeline_input, retriever))
    hypotheses.extend(_cross_plant_analogy_hypotheses(kb, pipeline_input, retriever))
    hypotheses.extend(_counterfactual_parameter_hypotheses(kb, pipeline_input, retriever))
    hypotheses.extend(_prediction_guided_hypotheses(kb, pipeline_input, retriever))
    return _dedupe(hypotheses)[:18]


def _numeric_priority_hypotheses(kb: KnowledgeBase, pipeline_input: PipelineInput, retriever: HybridRetriever) -> list[Hypothesis]:
    ranked = sorted(
        kb.size_classes,
        key=lambda r: max(r.element28_tonnes or 0, r.element29_tonnes or 0),
        reverse=True,
    )
    hypotheses: list[Hypothesis] = []
    for rec in ranked[:8]:
        element = "element28" if (rec.element28_tonnes or 0) >= (rec.element29_tonnes or 0) else "element29"
        tonnes = rec.element28_tonnes if element == "element28" else rec.element29_tonnes
        query = f"{rec.plant} {rec.stream} {rec.size_class} гидроциклон классификация флотация извлекаемый металл"
        evidence = _numeric_evidence(rec, element, tonnes) + retriever.retrieve(query, top_k=3)
        coarse = rec.size_class.strip().startswith("+") or "125" in rec.size_class
        if coarse:
            title = f"{rec.plant}: доклассификация и доизмельчение класса {rec.size_class}"
            change = f"Вывести класс {rec.size_class} в отдельный контур классификации/доизмельчения с контролем возвратной нагрузки."
            effect = "Снизить потери закрытого Pnt/Cp за счет раскрытия сростков перед возвратом во флотацию."
        else:
            title = f"{rec.plant}: отдельный режим флотации для класса {rec.size_class}"
            change = f"Настроить плотность пульпы, время контакта и дозирование реагентов для класса {rec.size_class}."
            effect = "Улучшить извлечение тонких раскрытых и частично раскрытых сульфидов без роста шламовых потерь."
        hypotheses.append(
            _make_hypothesis(
                title=title,
                target_kpi=pipeline_input.target_kpi,
                proposed_change=change,
                expected_effect=effect,
                plant=rec.plant,
                stream=rec.stream,
                size_class=rec.size_class,
                element=element,
                evidence=evidence,
                generator="numeric_priority",
            )
        )
    return hypotheses


def _expert_seed_hypotheses(kb: KnowledgeBase, pipeline_input: PipelineInput, retriever: HybridRetriever) -> list[Hypothesis]:
    seeds = [
        ("Настройка песковых насадок гидроциклонов", "Сравнить насадки 12 и 8 мм на контуре с высоким вкладом грубого класса."),
        ("Тонкое грохочение после второй стадии измельчения", "Поставить тонкое грохочение как альтернативу магнитной сепарации надцелевого класса."),
        ("Перераспределение фронта контрольной флотации", "Увеличить время первой контрольной операции для хвостов с высоким извлекаемым металлом."),
        ("Автоматизация подачи воды в мельницы", "Стабилизировать плотность пульпы и гранулометрию питания флотации."),
        ("Контроль гранулометрии после конусных дробилок", "Снизить вариабельность питания мельниц и долю +125 мкм в хвостах."),
    ]
    hypotheses = []
    for title, change in seeds:
        evidence = retriever.retrieve(title + " " + change, top_k=4)
        hypotheses.append(
            _make_hypothesis(
                title=title,
                target_kpi=pipeline_input.target_kpi,
                proposed_change=change,
                expected_effect="Повысить извлечение Ni/Cu из хвостов через более стабильный режим измельчения, классификации и флотации.",
                plant=None,
                stream=None,
                size_class=None,
                element="both",
                evidence=evidence,
                generator="expert_seed",
            )
        )
    return hypotheses


def _cross_plant_analogy_hypotheses(kb: KnowledgeBase, pipeline_input: PipelineInput, retriever: HybridRetriever) -> list[Hypothesis]:
    plants = sorted({rec.plant for rec in kb.size_classes})
    if len(plants) < 2:
        return []
    evidence = retriever.retrieve("классификация хвостов возврат в голову процесса гидроциклон", top_k=4)
    return [
        _make_hypothesis(
            title="Перенос лучших режимов классификации между фабриками",
            target_kpi=pipeline_input.target_kpi,
            proposed_change="Сравнить распределение потерь по классам между фабриками и перенести режимы с меньшей долей грубых извлекаемых потерь.",
            expected_effect="Сократить поиск настроек за счет аналогии между потоками хвостов с похожей минералогией.",
            plant=None,
            stream="межфабричное сравнение",
            size_class=None,
            element="both",
            evidence=evidence,
            generator="analogy",
        )
    ]


def _counterfactual_parameter_hypotheses(kb: KnowledgeBase, pipeline_input: PipelineInput, retriever: HybridRetriever) -> list[Hypothesis]:
    evidence = retriever.retrieve("плотность пульпы время контакта реагентный режим флотация", top_k=4)
    return [
        _make_hypothesis(
            title="A/B-карта плотности пульпы и времени агитации",
            target_kpi=pipeline_input.target_kpi,
            proposed_change="Провести матрицу испытаний плотности пульпы и времени контакта перед основной/контрольной флотацией.",
            expected_effect="Найти режим, где тонкие сульфидные частицы успевают закрепляться на пузырьках без чрезмерного уноса породы.",
            plant=None,
            stream="отвальные хвосты",
            size_class="-10",
            element="both",
            evidence=evidence,
            generator="counterfactual",
        )
    ]


def _prediction_guided_hypotheses(kb: KnowledgeBase, pipeline_input: PipelineInput, retriever: HybridRetriever) -> list[Hypothesis]:
    targets = [rec for rec in kb.extractability if rec.extractable]
    if not targets:
        return []
    top = max(targets, key=lambda rec: (rec.element28_tonnes or 0) + (rec.element29_tonnes or 0))
    evidence = retriever.retrieve(f"{top.plant} {top.stream} извлекаемый металл прогноз потерь", top_k=4)
    return [
        _make_hypothesis(
            title=f"{top.plant}: прогнозно-управляемый выбор хвостового потока для первичного теста",
            target_kpi=pipeline_input.target_kpi,
            proposed_change="Начать лабораторный план с потока, где максимальный суммарный извлекаемый металл по минералогии.",
            expected_effect="Повысить шанс быстрого положительного эффекта за счет выбора максимального expected value.",
            plant=top.plant,
            stream=top.stream,
            size_class=None,
            element="both",
            evidence=evidence,
            generator="prediction_guided",
        )
    ]


def _make_hypothesis(
    title: str,
    target_kpi: str,
    proposed_change: str,
    expected_effect: str,
    plant: str | None,
    stream: str | None,
    size_class: str | None,
    element: str,
    evidence: list[Evidence],
    generator: str,
) -> Hypothesis:
    text = f"{proposed_change} Это может {expected_effect.lower()}"
    chain = [
        f"Process: {proposed_change}",
        f"Structure/particle state: изменение раскрытия или распределения класса {size_class or 'целевого'}",
        f"Property: рост извлечения {element}",
        f"KPI: {target_kpi}",
        "Business: снижение металла в отвальных хвостах и рост потенциальной выручки",
    ]
    return Hypothesis(
        id=_hyp_id(title, proposed_change),
        title=title,
        hypothesis_text=text,
        target_kpi=target_kpi,
        proposed_change=proposed_change,
        expected_effect=expected_effect,
        material_process_scope="Флотационное обогащение Ni/Cu хвостов",
        target_plant=plant,
        target_stream=stream,
        target_size_class=size_class,
        target_element=element,  # type: ignore[arg-type]
        causal_chain=chain,
        evidence=evidence,
        novelty_rationale="Гипотеза привязана к конкретному классу крупности, потоку и evidence, а не является общей рекомендацией.",
        risks=[
            "Рост циркуляционной нагрузки при возврате класса в голову процесса.",
            "Переизмельчение и рост шламования при чрезмерном доизмельчении.",
            "Потенциальное ухудшение качества концентрата при изменении реагентного режима.",
        ],
        business_value_rationale="Приоритет определяется тоннажем потенциально извлекаемого металла и низкой стоимостью проверки на существующем оборудовании.",
        validation_plan=[
            ValidationStep(step="Подтвердить минералогию целевого класса повторным анализом", success_metric="расхождение по Ni/Cu < 10%"),
            ValidationStep(step="Провести лабораторный batch-тест режима", success_metric="снижение элемента в хвосте >= 3%"),
            ValidationStep(step="Пилот на одной технологической линии", success_metric="устойчивый эффект 3 смены без ухудшения концентрата"),
        ],
        generator=generator,
    )


def _numeric_evidence(rec, element: str, tonnes: float | None) -> list[Evidence]:
    return [
        Evidence(
            id=f"numeric:{rec.source.source_id}:{element}",
            text=f"{rec.plant} {rec.stream} класс {rec.size_class}: {element} потери {tonnes or 0:.1f} т.",
            source=rec.source,
            relevance=1.0,
        )
    ]


def _hyp_id(title: str, change: str) -> str:
    return hashlib.sha1(f"{title}|{change}".encode("utf-8")).hexdigest()[:10]


def _dedupe(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    seen: set[str] = set()
    result: list[Hypothesis] = []
    for hypothesis in hypotheses:
        key = hypothesis.id
        if key in seen:
            continue
        seen.add(key)
        result.append(hypothesis)
    return result

