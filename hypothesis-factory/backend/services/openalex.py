from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Iterable

from backend.schemas import SourceDocument
from backend.services.corpus_db import stable_hash

DEFAULT_TOPICS = [
    "T14184",  # Metallurgy and Material Science
    "T12959",  # Engineering and Materials Science Studies
    "T13129",  # Material Properties and Applications
    "T10192",  # Catalytic Processes in Materials Science
    "T11948",  # Machine Learning in Materials Science
    "T13552",  # Advanced Materials Characterization Techniques
    "T13685",  # Material Science and Thermodynamics
]

DEFAULT_SEARCH = (
    "materials science metallurgy alloys composites ceramics polymers "
    "flotation extractive metallurgy tailings beneficiation"
)

USER_AGENT = "hackNOR-hypothesis-factory/1.0 (mailto:andy@local)"


def fetch_openalex_works(
    *,
    search: str | None = None,
    topic_ids: list[str] | None = None,
    year_from: int = 2015,
    year_to: int = 2024,
    per_page: int = 50,
    page: int = 1,
    limit: int | None = None,
    mailto: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch article metadata from OpenAlex (legal OA metadata source)."""

    filters: list[str] = [f"publication_year:{year_from}-{year_to}", "type:article"]
    topics = topic_ids or DEFAULT_TOPICS
    if topics:
        topic_filter = "|".join(
            topic_id if topic_id.startswith("http") else f"https://openalex.org/{topic_id}"
            for topic_id in topics
        )
        filters.append(f"primary_topic.id:{topic_filter}")
    query: dict[str, str] = {
        "filter": ",".join(filters),
        "per-page": str(per_page),
        "page": str(page),
        "select": "id,doi,title,publication_year,abstract_inverted_index,primary_location,cited_by_count,topics,open_access,authorships",
        "sort": "cited_by_count:desc",
    }
    # OpenAlex ANDs `search` with topic filters; combined queries often return zero rows.
    if search and not topics:
        query["search"] = search
    if mailto:
        query["mailto"] = mailto

    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))

    results = [_normalize_work(item) for item in payload.get("results", [])]
    if limit is not None:
        return results[:limit]
    return results


def build_openalex_document(payload: dict[str, Any]) -> SourceDocument:
    work_id = str(payload.get("openalex_id") or payload.get("id") or "unknown")
    short_id = work_id.rsplit("/", 1)[-1]
    title = str(payload.get("title") or "Untitled")
    doi = payload.get("doi")
    text = _openalex_text(payload)
    path = f"openalex://{short_id}"
    if doi:
        path = f"openalex://{doi.replace('https://doi.org/', '')}"
    return SourceDocument(
        id=stable_hash(f"openalex:{short_id}"),
        path=path,
        source_type="openalex",
        title=title,
        text=text,
        metadata={
            "provider": "OpenAlex",
            "license": "CC0",
            "openalex_id": work_id,
            "doi": doi,
            "publication_year": payload.get("publication_year"),
            "cited_by_count": payload.get("cited_by_count"),
            "journal": (payload.get("journal") or {}).get("display_name"),
            "is_oa": (payload.get("open_access") or {}).get("is_oa"),
            "oa_url": (payload.get("open_access") or {}).get("oa_url"),
            "topics": [topic.get("display_name") for topic in payload.get("topics", []) if topic.get("display_name")],
        },
    )


def build_openalex_records(payloads: Iterable[dict[str, Any]]) -> list[tuple[SourceDocument, dict[str, Any]]]:
    return [(build_openalex_document(payload), payload) for payload in payloads]


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inverted_index.items():
        for idx in idxs:
            positions[idx] = word
    if not positions:
        return ""
    return " ".join(positions[i] for i in sorted(positions))


def _normalize_work(item: dict[str, Any]) -> dict[str, Any]:
    doi = item.get("doi")
    journal = (item.get("primary_location") or {}).get("source") or {}
    topics = []
    for topic in item.get("topics") or []:
        topics.append(
            {
                "id": topic.get("id"),
                "display_name": topic.get("display_name"),
                "score": topic.get("score"),
            }
        )
    authors = []
    for authorship in item.get("authorships") or []:
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if name:
            authors.append(name)
    abstract = reconstruct_abstract(item.get("abstract_inverted_index"))
    return {
        "openalex_id": item.get("id"),
        "doi": doi,
        "title": item.get("title"),
        "publication_year": item.get("publication_year"),
        "abstract": abstract,
        "cited_by_count": item.get("cited_by_count"),
        "journal": {
            "display_name": journal.get("display_name"),
            "issn": journal.get("issn"),
        },
        "authors": authors,
        "topics": topics,
        "open_access": item.get("open_access") or {},
        "landing_page_url": (item.get("primary_location") or {}).get("landing_page_url"),
        "pdf_url": (item.get("primary_location") or {}).get("pdf_url"),
    }


def _openalex_text(payload: dict[str, Any]) -> str:
    lines = [
        f"Title: {payload.get('title', 'Untitled')}",
        f"DOI: {payload.get('doi', 'n/a')}",
        f"Year: {payload.get('publication_year', 'n/a')}",
        f"Citations: {payload.get('cited_by_count', 0)}",
    ]
    journal = payload.get("journal") or {}
    if journal.get("display_name"):
        lines.append(f"Journal: {journal['display_name']}")
    if payload.get("authors"):
        lines.append("Authors: " + "; ".join(payload["authors"][:12]))
    if payload.get("topics"):
        lines.append("Topics: " + "; ".join(topic["display_name"] for topic in payload["topics"][:6] if topic.get("display_name")))
    oa = payload.get("open_access") or {}
    if oa.get("oa_url"):
        lines.append(f"Open access URL: {oa['oa_url']}")
    lines.append("")
    lines.append("Abstract:")
    lines.append(payload.get("abstract") or "(no abstract in OpenAlex)")
    lines.append("")
    lines.append("Source: OpenAlex metadata API. License: CC0.")
    return "\n".join(lines)
