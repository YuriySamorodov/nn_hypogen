import asyncio
import re
from pathlib import Path

import chainlit as cl

from src.export import write_export_files
from src.hypotheses import (
    HypothesisGenerationError,
    generate_hypotheses,
    hypotheses_to_markdown,
)
from src.graph_retrieval import retrieve_graph_facts
from src.retrieval import retrieve_context
from src.schemas import HypothesisBatch
from src.settings import get_settings


EXAMPLE_REQUESTS = [
    (
        "Цель: повысить жаропрочность никелевого сплава на 15%.\n"
        "Ограничения: ниобий не выше 0.3%, без вакуумной плавки, бюджет на проверку 2 недели.\n"
        "Количество гипотез: 3"
    ),
    (
        "Цель: снизить себестоимость шихты без потери прочности.\n"
        "Ограничения: вторичное сырье до 20%, сера ниже 0.015%, стандартная термообработка.\n"
        "Количество гипотез: 3"
    ),
    (
        "Цель: повысить извлечение меди и никеля при флотации на 4 процентных пункта.\n"
        "Ограничения: без покупки нового оборудования, реагенты из текущего склада.\n"
        "Количество гипотез: 2"
    ),
]


def parse_user_request(text: str) -> tuple[str, str, int]:
    target_match = re.search(
        r"(?:цель|target)\s*:\s*(.+?)(?:\n|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    constraints_match = re.search(
        r"(?:ограничения|constraints)\s*:\s*(.+?)(?:\n|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    count_match = re.search(
        r"(?:количество гипотез|hypotheses|count)\s*:\s*(\d+)",
        text,
        flags=re.IGNORECASE,
    )

    target = target_match.group(1).strip() if target_match else text.strip()
    constraints = constraints_match.group(1).strip() if constraints_match else "Не указаны"
    count = int(count_match.group(1)) if count_match else 3
    count = max(1, min(count, 5))
    return target, constraints, count


async def _send_export(batch: HypothesisBatch) -> None:
    settings = get_settings()
    markdown = hypotheses_to_markdown(batch)
    md_path, docx_path = await asyncio.to_thread(
        write_export_files,
        batch,
        Path(settings.export_dir),
    )
    await cl.Message(
        content=(
            f"{markdown}\n\n"
            "Файлы отчёта сохранены — скачайте Markdown или DOCX ниже."
        ),
        elements=[
            cl.File(name=md_path.name, path=str(md_path), display="inline"),
            cl.File(name=docx_path.name, path=str(docx_path), display="inline"),
        ],
    ).send()


@cl.on_chat_start
async def on_chat_start() -> None:
    examples = "\n\n".join(f"```text\n{example}\n```" for example in EXAMPLE_REQUESTS)
    await cl.Message(
        content=(
            "# Фабрика гипотез\n\n"
            "Введите целевое свойство, ограничения и количество гипотез. "
            "Сначала запустите `python -m src.ingest --recreate`, чтобы заполнить Qdrant.\n\n"
            f"{examples}\n\n"
            "Команда `/export` — повторить последний отчёт и скачать файлы MD/DOCX."
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    content = message.content.strip()
    if content.lower() == "/export":
        batch = cl.user_session.get("last_batch")
        if not batch:
            await cl.Message(
                content="Пока нечего экспортировать: сначала сгенерируйте гипотезы."
            ).send()
            return
        await _send_export(batch)
        return

    target_property, constraints, hypothesis_count = parse_user_request(content)
    settings = get_settings()

    progress = cl.Message(content="Ищу релевантные фрагменты в Qdrant и факты в графе знаний...")
    await progress.send()

    try:
        query = f"{target_property}\n{constraints}"
        chunks, graph_facts = await asyncio.gather(
            asyncio.to_thread(
                retrieve_context,
                query,
                settings,
            ),
            asyncio.to_thread(
                retrieve_graph_facts,
                query,
                settings,
            ),
        )
    except Exception as exc:
        progress.content = (
            "Не удалось получить контекст из Qdrant. "
            "Проверьте, что запущен `docker compose up -d qdrant` "
            "и выполнен `python -m src.ingest --recreate`.\n\n"
            f"Ошибка: `{type(exc).__name__}: {exc}`"
        )
        await progress.update()
        return

    progress.content = "Генерирую структурированные гипотезы через DeepSeek..."
    await progress.update()

    try:
        batch = await asyncio.to_thread(
            generate_hypotheses,
            target_property=target_property,
            constraints=constraints,
            chunks=chunks,
            graph_facts=graph_facts,
            hypothesis_count=hypothesis_count,
            settings=settings,
        )
    except HypothesisGenerationError as exc:
        progress.content = f"Генерация не удалась: {exc}"
        await progress.update()
        return
    except Exception as exc:
        progress.content = f"Неожиданная ошибка: `{type(exc).__name__}: {exc}`"
        await progress.update()
        return

    cl.user_session.set("last_batch", batch)
    markdown = hypotheses_to_markdown(batch)
    md_path, docx_path = await asyncio.to_thread(
        write_export_files,
        batch,
        Path(settings.export_dir),
    )
    progress.content = (
        f"{markdown}\n\n"
        "Отчёт сохранён — скачайте файлы ниже или напишите `/export` для повторной выгрузки."
    )
    progress.elements = [
        cl.File(name=md_path.name, path=str(md_path), display="inline"),
        cl.File(name=docx_path.name, path=str(docx_path), display="inline"),
    ]
    await progress.update()
