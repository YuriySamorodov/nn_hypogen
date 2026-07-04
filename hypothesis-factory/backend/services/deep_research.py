"""Deep Research (DeepSearch-style) over the ingested corpus.

Pipeline: question -> LLM query decomposition -> multi-query vector retrieval
(Qdrant/KG) -> evidence pooling with numbered sources -> LLM synthesis with
inline [n] citations. Falls back to an extractive answer when no LLM is
available, so the feature never hard-fails.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.config import settings
from backend.schemas import Evidence, KnowledgeBase
from backend.services.llm import (
    LLMClient,
    deepseek_research_client,
    glm_research_client,
    research_llm_client,
)
from backend.services.retrieval import KGVectorRetriever, _resolve_run_id
from backend.services.web_search import web_search_evidence

DEFAULT_RUN_ID = "0984978570c27819"

_SYNTH_SYSTEM = (
    "Ты — исследовательский ассистент по обогащению руды (Норникель: флотация Ni/Cu, "
    "измельчение, классификация, гидроциклоны, хвосты, реагентные режимы). "
    "Отвечай на вопрос пользователя, опираясь ТОЛЬКО на приведённые пронумерованные источники. "
    "Источники бывают внутренние (наш корпус) и внешние из интернета (помечены [web]/[openalex], с URL). "
    "Каждое фактическое утверждение подкрепляй ссылкой в квадратных скобках вида [n] (номер источника). "
    "Разрешено объединять несколько источников: [1][3]. "
    "Когда факт взят из интернета, а не из нашей БД — отмечай это явно. "
    "Если данных недостаточно — прямо скажи об этом, не выдумывай. "
    "Пиши по-русски, структурированно: краткое резюме, затем детали, затем вывод/рекомендация."
)

_DECOMPOSE_SYSTEM = (
    "Ты планировщик поиска по научно-техническому корпусу по обогащению руды (Ni/Cu флотация). "
    "Разбей вопрос пользователя на 2-4 самостоятельных поисковых под-запроса на русском языке, "
    "покрывающих разные аспекты (процесс, параметры, оборудование, метрики). "
    'Верни СТРОГО JSON вида {"queries": ["...", "..."]} без пояснений.'
)


@dataclass
class Citation:
    n: int
    filename: str
    source_id: str
    source_type: str
    section: str
    relevance: float
    text: str


@dataclass
class ResearchStep:
    sub_query: str
    hits: int


@dataclass
class DeepResearchResult:
    question: str
    answer: str
    sub_queries: list[str]
    citations: list[Citation]
    steps: list[ResearchStep]
    provider: str
    run_id: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}
    return {}


def _decompose(llm: LLMClient, question: str, max_subqueries: int) -> list[str]:
    if max_subqueries <= 1:
        return [question]
    try:
        resp = llm.complete_json(_DECOMPOSE_SYSTEM, f"Вопрос: {question}")
        data = _extract_json(resp.text)
        queries = [str(q).strip() for q in (data.get("queries") or []) if str(q).strip()]
    except Exception:
        queries = []
    ordered = [question] + [q for q in queries if q.lower() != question.lower()]
    seen: set[str] = set()
    result: list[str] = []
    for q in ordered:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(q)
        if len(result) >= max_subqueries:
            break
    return result or [question]


def _collect_evidence(
    run_id: str,
    mode: str,
    sub_queries: list[str],
    top_k: int,
    database_url: str | None,
) -> tuple[list[Evidence], list[ResearchStep], list[str]]:
    retriever = KGVectorRetriever(KnowledgeBase(chunks=[]), run_id, mode, database_url=database_url)
    pool: dict[str, Evidence] = {}
    steps: list[ResearchStep] = []
    warnings: list[str] = []
    for query in sub_queries:
        try:
            hits = retriever.retrieve(query, top_k=top_k)
        except Exception as exc:  # empty-KB fallback or qdrant error
            warnings.append(f"retrieve_failed[{query[:40]}]: {exc}")
            hits = []
        warnings.extend(retriever.last_warnings)
        steps.append(ResearchStep(sub_query=query, hits=len(hits)))
        for ev in hits:
            existing = pool.get(ev.id)
            if existing is None or ev.relevance > existing.relevance:
                pool[ev.id] = ev
    ranked = sorted(pool.values(), key=lambda e: e.relevance, reverse=True)
    return ranked, steps, warnings


def _resolve_web_backends(web_backends: list[str] | None) -> tuple[str, ...]:
    if web_backends is not None:
        return tuple(b.strip() for b in web_backends if b and b.strip())
    return tuple(b.strip() for b in settings.web_search_backends.split(",") if b.strip())


def _augment_with_web(
    corpus: list[Evidence],
    sub_queries: list[str],
    *,
    web: bool,
    web_max: int,
    web_backends: list[str] | None,
    max_context: int,
    steps: list[ResearchStep],
    warnings: list[str],
) -> list[Evidence]:
    """Merge internet evidence into the pool, reserving slots so web is always
    represented (per the 'always augment' policy). Corpus fills the rest."""
    if not web or web_max <= 0:
        return corpus[:max_context]
    backends = _resolve_web_backends(web_backends)
    if not backends:
        return corpus[:max_context]
    web_ev, w_warn = web_search_evidence(
        sub_queries[:2] or sub_queries,
        max_per_query=settings.web_search_max_results,
        backends=backends,
    )
    warnings.extend(w_warn)
    steps.append(ResearchStep(sub_query="[web] " + "; ".join(sub_queries[:2]), hits=len(web_ev)))
    if not web_ev:
        return corpus[:max_context]
    n_web = min(web_max, len(web_ev))
    n_corpus = max(0, max_context - n_web)
    return corpus[:n_corpus] + web_ev[:n_web]


def _context_block(evidence: list[Evidence]) -> str:
    blocks = []
    for i, ev in enumerate(evidence, 1):
        st = ev.source.source_type
        origin = "[web]" if st == "web" else "[openalex]" if st == "openalex" else "[корпус]"
        blocks.append(f"[{i}] {origin} источник: {ev.source.filename}\n{ev.text}")
    return "\n\n".join(blocks)


def _extractive_answer(question: str, evidence: list[Evidence]) -> str:
    lines = [
        f"LLM недоступен — экстрактивная сводка по запросу: «{question}».",
        "",
        "Наиболее релевантные фрагменты корпуса:",
    ]
    for i, ev in enumerate(evidence[:6], 1):
        snippet = ev.text.strip().replace("\n", " ")
        if len(snippet) > 320:
            snippet = snippet[:320] + "…"
        lines.append(f"- [{i}] ({ev.source.filename}, rel={ev.relevance:.2f}) {snippet}")
    return "\n".join(lines)


def _synthesize(llm: LLMClient, question: str, evidence: list[Evidence]) -> tuple[str, str]:
    context = _context_block(evidence)
    user = (
        f"Вопрос: {question}\n\n"
        f"Источники (используй номера для цитирования):\n{context}\n\n"
        "Дай развёрнутый ответ с цитированием [n]. В конце добавь строку 'Вывод:' с краткой рекомендацией."
    )
    resp = llm.chat_text(_SYNTH_SYSTEM, user, temperature=0.3, max_tokens=1800)
    return resp.text, resp.provider


def run_deep_research(
    question: str,
    *,
    run_id: str = DEFAULT_RUN_ID,
    mode: str = "qdrant",
    top_k: int = 8,
    max_subqueries: int = 3,
    max_context: int = 12,
    web: bool = True,
    web_max: int = 6,
    web_backends: list[str] | None = None,
    llm: LLMClient | None = None,
    database_url: str | None = None,
) -> DeepResearchResult:
    question = (question or "").strip()
    if not question:
        raise ValueError("question is required")
    run_id = _resolve_run_id(run_id, database_url)
    llm = llm or research_llm_client()

    sub_queries = _decompose(llm, question, max_subqueries)
    corpus, steps, warnings = _collect_evidence(run_id, mode, sub_queries, top_k, database_url)
    evidence = _augment_with_web(
        corpus, sub_queries, web=web, web_max=web_max, web_backends=web_backends,
        max_context=max_context, steps=steps, warnings=warnings,
    )

    citations = [
        Citation(
            n=i,
            filename=ev.source.filename,
            source_id=ev.source.source_id,
            source_type=ev.source.source_type,
            section=ev.source.section,
            relevance=round(ev.relevance, 3),
            text=ev.text,
        )
        for i, ev in enumerate(evidence, 1)
    ]

    if not evidence:
        return DeepResearchResult(
            question=question,
            answer="Ни в корпусе, ни в вебе не нашлось релевантных фрагментов. "
            "Проверь run_id (собран ли KG/Qdrant), включи web или переформулируй вопрос.",
            sub_queries=sub_queries,
            citations=[],
            steps=steps,
            provider="none",
            run_id=run_id,
            warnings=warnings,
        )

    try:
        answer, provider = _synthesize(llm, question, evidence)
    except Exception as exc:
        warnings.append(f"llm_synthesis_failed: {exc}")
        answer, provider = _extractive_answer(question, evidence), "extractive"

    return DeepResearchResult(
        question=question,
        answer=answer,
        sub_queries=sub_queries,
        citations=citations,
        steps=steps,
        provider=provider,
        run_id=run_id,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Ensemble Deep Research: DeepSeek + GLM (latest) with a GLM long-thinking judge
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = (
    "Ты — старший научный редактор по обогащению руды Норникеля (флотация Ni/Cu). "
    "Тебе даны: вопрос, общий набор пронумерованных источников и ДВА независимых черновика ответа "
    "от разных моделей (DeepSeek и GLM). Синтезируй один финальный отчёт. Требования:\n"
    "1) Опирайся ТОЛЬКО на источники; каждое утверждение цитируй [n].\n"
    "2) Источники бывают внутренние (наш корпус) и внешние из интернета ([web]/[openalex], с URL) — "
    "отмечай, когда факт взят из интернета, а не из нашей БД.\n"
    "3) Возьми лучшее из обоих черновиков, убери галлюцинации и утверждения без опоры на источники.\n"
    "4) Явно вынеси раздел '## Расхождения моделей' — где черновики противоречат друг другу или источникам.\n"
    "5) В конце — '## Вывод' с рекомендацией и '## Уверенность' (низкая/средняя/высокая + почему).\n"
    "Пиши по-русски, структурированно."
)


@dataclass
class ModelDraft:
    provider: str
    model: str
    answer: str
    reasoning: str | None = None
    error: str | None = None


@dataclass
class EnsembleResult:
    question: str
    run_id: str
    final_answer: str
    judge_provider: str
    plan_provider: str
    sub_queries: list[str]
    drafts: list[ModelDraft]
    citations: list[Citation]
    steps: list[ResearchStep]
    warnings: list[str] = field(default_factory=list)
    judge_reasoning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _draft(llm: LLMClient, question: str, evidence: list[Evidence]) -> ModelDraft:
    provider = getattr(llm, "provider", "?")
    model = getattr(llm, "model", "?")
    try:
        answer, _ = _synthesize(llm, question, evidence)
        # reasoning captured on the raw response is not returned by _synthesize; redo lightly
        return ModelDraft(provider=provider, model=model, answer=answer)
    except Exception as exc:
        return ModelDraft(provider=provider, model=model, answer="", error=str(exc))


def run_deep_research_ensemble(
    question: str,
    *,
    run_id: str = DEFAULT_RUN_ID,
    mode: str = "qdrant",
    top_k: int = 8,
    max_subqueries: int = 4,
    max_context: int = 14,
    web: bool = True,
    web_max: int = 6,
    web_backends: list[str] | None = None,
    database_url: str | None = None,
) -> EnsembleResult:
    question = (question or "").strip()
    if not question:
        raise ValueError("question is required")
    run_id = _resolve_run_id(run_id, database_url)

    deepseek = deepseek_research_client(fast=True)
    glm_plain = glm_research_client(thinking=False)
    glm_think = glm_research_client(thinking=True, reasoning_effort="high")
    fallback = research_llm_client()

    warnings: list[str] = []

    # 1) Planner: prefer GLM long-thinking, else fallback.
    planner = glm_think or fallback
    plan_provider = getattr(planner, "provider", "?")
    sub_queries = _decompose(planner, question, max_subqueries)

    # 2) Shared retrieval (corpus) + internet augmentation into one pool.
    corpus, steps, retr_warn = _collect_evidence(run_id, mode, sub_queries, top_k, database_url)
    warnings.extend(retr_warn)
    evidence = _augment_with_web(
        corpus, sub_queries, web=web, web_max=web_max, web_backends=web_backends,
        max_context=max_context, steps=steps, warnings=warnings,
    )

    citations = [
        Citation(
            n=i,
            filename=ev.source.filename,
            source_id=ev.source.source_id,
            source_type=ev.source.source_type,
            section=ev.source.section,
            relevance=round(ev.relevance, 3),
            text=ev.text,
        )
        for i, ev in enumerate(evidence, 1)
    ]

    if not evidence:
        return EnsembleResult(
            question=question,
            run_id=run_id,
            final_answer="В корпусе не нашлось релевантных фрагментов. Проверь run_id (собран ли Qdrant) или переформулируй вопрос.",
            judge_provider="none",
            plan_provider=plan_provider,
            sub_queries=sub_queries,
            drafts=[],
            citations=[],
            steps=steps,
            warnings=warnings,
        )

    # 3) Parallel independent drafts (DeepSeek + GLM).
    draft_clients: list[LLMClient] = []
    if deepseek is not None:
        draft_clients.append(deepseek)
    if glm_plain is not None:
        draft_clients.append(glm_plain)
    if not draft_clients:
        draft_clients.append(fallback)

    with ThreadPoolExecutor(max_workers=len(draft_clients)) as pool:
        drafts = list(pool.map(lambda c: _draft(c, question, evidence), draft_clients))

    good_drafts = [d for d in drafts if d.answer and not d.error]
    for d in drafts:
        if d.error:
            warnings.append(f"draft_failed[{d.provider}]: {d.error}")

    # 4) Judge/merge: GLM long-thinking preferred.
    judge = glm_think or deepseek or fallback
    judge_provider = getattr(judge, "provider", "?")
    judge_reasoning: str | None = None

    if not good_drafts:
        final_answer = _extractive_answer(question, evidence)
        judge_provider = "extractive"
    elif len(good_drafts) == 1:
        final_answer = good_drafts[0].answer
        judge_provider = good_drafts[0].provider + "(single)"
    else:
        context = _context_block(evidence)
        drafts_block = "\n\n".join(
            f"### Черновик {i} — модель {d.provider}/{d.model}\n{d.answer}" for i, d in enumerate(good_drafts, 1)
        )
        user = (
            f"Вопрос: {question}\n\n"
            f"Источники (для цитирования [n]):\n{context}\n\n"
            f"{drafts_block}\n\n"
            "Синтезируй финальный отчёт по инструкции."
        )
        try:
            resp = judge.chat_text(_JUDGE_SYSTEM, user, temperature=0.2, max_tokens=2400)
            final_answer = resp.text
            judge_reasoning = resp.reasoning
        except Exception as exc:
            warnings.append(f"judge_failed: {exc}")
            final_answer = max(good_drafts, key=lambda d: len(d.answer)).answer
            judge_provider = "fallback-longest-draft"

    return EnsembleResult(
        question=question,
        run_id=run_id,
        final_answer=final_answer,
        judge_provider=judge_provider,
        plan_provider=plan_provider,
        sub_queries=sub_queries,
        drafts=drafts,
        citations=citations,
        steps=steps,
        warnings=warnings,
        judge_reasoning=judge_reasoning,
    )


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Deep Research over the corpus KG/Qdrant")
    parser.add_argument("--question", required=True)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--mode", default="qdrant", choices=["auto", "qdrant", "kg", "tfidf"])
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--max-subqueries", type=int, default=3)
    parser.add_argument("--max-context", type=int, default=12)
    parser.add_argument("--ensemble", action="store_true", help="DeepSeek + GLM ensemble with GLM judge")
    parser.add_argument("--no-web", action="store_true", help="disable internet augmentation (GLM web_search + OpenAlex)")
    parser.add_argument("--web-max", type=int, default=6)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    web = not args.no_web
    if args.ensemble:
        res = run_deep_research_ensemble(
            args.question,
            run_id=args.run_id,
            mode=args.mode,
            top_k=args.top_k,
            max_subqueries=args.max_subqueries,
            max_context=args.max_context,
            web=web,
            web_max=args.web_max,
        )
        if args.json:
            print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
            return 0
        print(f"plan={res.plan_provider} judge={res.judge_provider} run_id={res.run_id}")
        print(f"sub_queries={res.sub_queries}")
        for d in res.drafts:
            tag = d.error or f"{len(d.answer)} chars"
            print(f"  draft {d.provider}/{d.model}: {tag}")
        print("\n=== FINAL (ensemble) ===\n")
        print(res.final_answer)
        print("\n=== SOURCES ===")
        for c in res.citations:
            print(f"[{c.n}] ({c.source_type}) {c.filename} (rel={c.relevance})")
        if res.warnings:
            print("\nwarnings:", res.warnings)
        return 0

    result = run_deep_research(
        args.question,
        run_id=args.run_id,
        mode=args.mode,
        top_k=args.top_k,
        max_subqueries=args.max_subqueries,
        max_context=args.max_context,
        web=web,
        web_max=args.web_max,
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(f"provider={result.provider} run_id={result.run_id}")
    print(f"sub_queries={result.sub_queries}")
    print("\n=== ANSWER ===\n")
    print(result.answer)
    print("\n=== SOURCES ===")
    for c in result.citations:
        print(f"[{c.n}] ({c.source_type}) {c.filename} (rel={c.relevance})")
    if result.warnings:
        print("\nwarnings:", result.warnings)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
