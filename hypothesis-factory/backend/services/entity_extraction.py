from __future__ import annotations

import re

from backend.schemas import Entity, KnowledgeBase


PATTERNS = {
    "process": r"\b(флотац\w+|измельчен\w+|классификац\w+|доизмельчен\w+|гидроциклон\w+|грохочен\w+|обжиг\w+)\b",
    "equipment": r"\b(мельниц\w+|классификатор\w+|гидроциклон\w+|флотомашин\w+|дробилк\w+|насоск?\w+|сгустител\w+)\b",
    "mineral": r"\b(Pnt/Cp|Pnt|Cp|пентландит\w*|халькопирит\w*|пирротин\w*|миллерит\w*|валлериит\w*)\b",
    "size_class": r"(\+125|-125\s*\+?\s*71|-71\s*\+?\s*45|-45\s*\+?\s*20|-20\s*\+?\s*10|-10)\s*(?:мкм)?",
}


def extract_entities(kb: KnowledgeBase) -> list[Entity]:
    seen: set[tuple[str, str]] = set()
    entities: list[Entity] = []
    for chunk in kb.chunks:
        for entity_type, pattern in PATTERNS.items():
            for match in re.finditer(pattern, chunk.text, flags=re.IGNORECASE):
                name = " ".join(match.group(0).split())
                key = (entity_type, name.lower())
                if key in seen:
                    continue
                seen.add(key)
                entities.append(
                    Entity(
                        id=f"{entity_type}:{len(entities)+1}",
                        type=entity_type,
                        name=name,
                        normalized=name.lower(),
                        metadata={"first_document_id": chunk.document_id},
                    )
                )
    return entities

