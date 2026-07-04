"""Web augmentation for Deep Research.

Turns internet sources into the same `Evidence` objects the corpus retriever
produces, so DeepSeek/GLM synthesize and cite over a unified pool. Two backends,
no new API keys:

  - glm      -> Zhipu standalone Web Search API (general web)
  - openalex -> OpenAlex scholarly metadata (scientific, CC0)

Every hit is tagged with source_type web/openalex and the URL goes into
`source.filename` so citations carry clickable provenance. Failures degrade to
warnings and never raise.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from backend.config import settings
from backend.schemas import Evidence, SourceRef
from backend.services.corpus_db import stable_hash
from backend.services.llm import glm_research_client
from backend.services.openalex import fetch_openalex_works, reconstruct_abstract

DEFAULT_BACKENDS = ("glm", "openalex")


def _clip(text: str, limit: int = 900) -> str:
    text = (text or "").strip()
    return text[:limit]


def _rank_relevance(rank: int, base: float = 0.6) -> float:
    return max(0.2, base - 0.04 * rank)


def _glm_backend(queries: list[str], max_per_query: int, warnings: list[str]) -> list[Evidence]:
    client = glm_research_client()
    if client is None or not hasattr(client, "web_search"):
        warnings.append("web_glm_unavailable: GLM/ZAI key not set")
        return []
    out: list[Evidence] = []
    for query in queries:
        try:
            results = client.web_search(query, count=max_per_query)
        except Exception as exc:
            warnings.append(f"web_glm_failed[{query[:32]}]: {exc}")
            continue
        for rank, r in enumerate(results):
            link = str(r.get("link") or r.get("url") or "").strip()
            title = str(r.get("title") or link or "web result").strip()
            content = str(r.get("content") or r.get("snippet") or "").strip()
            date = str(r.get("publish_date") or "").strip()
            text = f"{title}\n{content}" + (f"\n(дата: {date})" if date else "")
            out.append(
                Evidence(
                    id=f"web:{stable_hash(link or title)}",
                    text=_clip(text),
                    source=SourceRef(
                        source_id=link or title,
                        source_type="web",
                        filename=link or title,
                        section="web",
                    ),
                    relevance=_rank_relevance(rank, base=0.6),
                )
            )
    return out


def _openalex_backend(queries: list[str], max_per_query: int, year_from: int, warnings: list[str]) -> list[Evidence]:
    out: list[Evidence] = []
    for query in queries:
        try:
            works = fetch_openalex_works(
                search=query,
                topic_ids=[],  # empty -> use free-text search instead of topic filter
                year_from=year_from,
                year_to=2026,
                per_page=max_per_query,
                limit=max_per_query,
                mailto=settings.openalex_mailto or settings.unpaywall_email,
            )
        except Exception as exc:
            warnings.append(f"web_openalex_failed[{query[:32]}]: {exc}")
            continue
        for rank, w in enumerate(works):
            title = str(w.get("title") or "Untitled")
            abstract = w.get("abstract") or reconstruct_abstract(w.get("abstract_inverted_index")) or ""
            doi = w.get("doi")
            url = doi or w.get("landing_page_url") or w.get("pdf_url") or str(w.get("openalex_id") or "")
            year = w.get("publication_year")
            text = f"{title} ({year})\n{abstract}"
            out.append(
                Evidence(
                    id=f"openalex:{stable_hash(url or title)}",
                    text=_clip(text),
                    source=SourceRef(
                        source_id=url or title,
                        source_type="openalex",
                        filename=url or title,
                        section="web",
                    ),
                    relevance=_rank_relevance(rank, base=0.55),
                )
            )
    return out


def web_search_evidence(
    queries: list[str],
    *,
    max_per_query: int = 4,
    backends: tuple[str, ...] | list[str] = DEFAULT_BACKENDS,
    year_from: int | None = None,
) -> tuple[list[Evidence], list[str]]:
    queries = [q for q in (queries or []) if q and q.strip()]
    if not queries:
        return [], []
    year_from = year_from if year_from is not None else settings.openalex_web_year_from
    warnings: list[str] = []
    tasks = []
    if "glm" in backends:
        tasks.append(("glm", lambda: _glm_backend(queries, max_per_query, warnings)))
    if "openalex" in backends:
        tasks.append(("openalex", lambda: _openalex_backend(queries, max_per_query, year_from, warnings)))
    if not tasks:
        return [], warnings

    collected: list[Evidence] = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        for evs in pool.map(lambda t: t[1](), tasks):
            collected.extend(evs)

    seen: set[str] = set()
    deduped: list[Evidence] = []
    for ev in sorted(collected, key=lambda e: e.relevance, reverse=True):
        key = ev.source.filename or ev.id
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ev)
    return deduped, warnings
