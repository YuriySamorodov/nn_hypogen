from __future__ import annotations

from backend.schemas import KnowledgeBase, Relation, SourceRef


def extract_relations(kb: KnowledgeBase) -> list[Relation]:
    relations: list[Relation] = []
    for rec in kb.size_classes:
        for element, tonnes in [("element28", rec.element28_tonnes), ("element29", rec.element29_tonnes)]:
            if tonnes and tonnes > 0:
                relations.append(
                    Relation(
                        source=f"{rec.plant}:{rec.stream}:{rec.size_class}",
                        relation="CONTAINS_LOSS",
                        target=element,
                        evidence_text=(
                            f"{rec.plant} {rec.stream} класс {rec.size_class}: "
                            f"{element} потери {tonnes:.1f} т."
                        ),
                        source_ref=rec.source,
                        confidence=0.95,
                    )
                )
    for rec in kb.extractability:
        relation = "POTENTIALLY_RECOVERABLE" if rec.extractable else "NOT_RECOVERABLE_WITH_CURRENT_TECH"
        target = f"{rec.plant}:{rec.stream}"
        text = (
            f"{target}: {relation}, Э28 {rec.element28_tonnes or 0:.1f} т, "
            f"Э29 {rec.element29_tonnes or 0:.1f} т."
        )
        relations.append(
            Relation(
                source=target,
                relation=relation,
                target="mineralogical_forms",
                evidence_text=text,
                source_ref=rec.source,
                confidence=0.90,
            )
        )
    return relations

