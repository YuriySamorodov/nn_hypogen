from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from backend.config import settings
from backend.schemas import SourceDocument
from backend.services.corpus_db import stable_hash


OPENALEX_BASE_URL = "https://api.openalex.org"
UNPAYWALL_BASE_URL = "https://api.unpaywall.org/v2"

def dedupe_queries(queries: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        clean = query.strip()
        key = clean.lower()
        if clean and key not in seen:
            deduped.append(clean)
            seen.add(key)
    return deduped


DEFAULT_MATERIALS_QUERY_PROFILES: dict[str, list[str]] = {
    "core": [
        "materials science",
        "materials chemistry",
        "solid state chemistry",
        "condensed matter materials",
        "crystallography materials",
        "phase stability materials",
        "defect chemistry materials",
        "nanomaterials chemistry",
        "ceramics materials chemistry",
        "semiconductor materials",
        "magnetic materials",
    ],
    "adjacent": [
        "chemical physics materials",
        "physical chemistry materials",
        "inorganic chemistry materials",
        "surface chemistry materials",
        "interface chemistry materials",
        "surface science materials",
        "adsorption energy materials",
        "catalysis materials",
        "thermodynamics materials",
        "kinetics materials",
        "diffusion materials",
        "materials spectroscopy",
        "solid state physics materials",
        "statistical mechanics materials",
    ],
    "mining": [
        "high entropy alloys",
        "metallurgy alloys",
        "extractive metallurgy",
        "process metallurgy",
        "hydrometallurgy materials",
        "pyrometallurgy materials",
        "powder metallurgy materials",
        "mineral processing chemistry",
        "flotation mineral surface chemistry",
        "sulfide minerals flotation",
        "tailings reprocessing flotation",
        "rare earth separation materials",
        "critical minerals processing",
        "corrosion materials",
        "coatings materials",
        "tribology materials",
        "wear resistant alloys",
    ],
    "energy": [
        "electrochemistry materials",
        "battery materials",
        "fuel cell materials",
        "supercapacitor materials",
        "hydrogen storage materials",
        "electrocatalysis materials",
        "photocatalysis materials",
        "thermoelectric materials",
        "photovoltaic materials",
        "membrane materials",
        "carbon capture materials",
        "solid electrolyte materials",
    ],
    "bio_soft": [
        "biomaterials",
        "biointerface materials",
        "tissue engineering materials",
        "hydrogel materials",
        "polymers materials chemistry",
        "polymer physics materials",
        "composite materials",
        "colloids materials",
        "rheology materials",
        "soft matter materials",
    ],
    "computational": [
        "computational materials science",
        "density functional theory materials",
        "materials informatics",
        "machine learning materials science",
        "molecular dynamics materials",
        "CALPHAD materials",
        "phase field modeling materials",
        "finite element materials",
        "high throughput DFT materials",
    ],
}

DEFAULT_MATERIALS_QUERY_PROFILE_ALIASES = {
    "bio-soft": "bio_soft",
    "bio": "bio_soft",
}

DEFAULT_MATERIALS_QUERY_PROFILES["full"] = dedupe_queries(
    [
        query
        for profile_name, profile_queries in DEFAULT_MATERIALS_QUERY_PROFILES.items()
        if profile_name != "full"
        for query in profile_queries
    ]
)

DEFAULT_MATERIALS_QUERIES = DEFAULT_MATERIALS_QUERY_PROFILES["full"]


def normalize_query_profile(profile: str | None) -> str:
    key = (profile or "full").strip().lower().replace("_", "-")
    return DEFAULT_MATERIALS_QUERY_PROFILE_ALIASES.get(key, key.replace("-", "_"))


def get_materials_queries(profile: str = "full") -> list[str]:
    normalized = normalize_query_profile(profile)
    if normalized not in DEFAULT_MATERIALS_QUERY_PROFILES:
        raise KeyError(f"unknown materials query profile: {profile}")
    return list(DEFAULT_MATERIALS_QUERY_PROFILES[normalized])


def get_materials_query_profile_categories(profile: str = "full") -> list[str]:
    normalized = normalize_query_profile(profile)
    if normalized == "full":
        return [key for key in DEFAULT_MATERIALS_QUERY_PROFILES if key != "full"]
    return [normalized]


def fetch_openalex_works_page(
    *,
    query: str,
    cursor: str = "*",
    per_page: int = 200,
    mailto: str | None = None,
    api_key: str | None = None,
    from_year: int | None = None,
    to_year: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "search": query,
        "per-page": min(max(per_page, 1), 200),
        "cursor": cursor,
        "select": ",".join(
            [
                "id",
                "doi",
                "title",
                "display_name",
                "publication_year",
                "publication_date",
                "type",
                "authorships",
                "primary_location",
                "best_oa_location",
                "open_access",
                "abstract_inverted_index",
                "concepts",
                "topics",
                "keywords",
                "referenced_works",
                "related_works",
                "cited_by_count",
                "ids",
            ]
        ),
    }
    filters = []
    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")
    if to_year:
        filters.append(f"to_publication_date:{to_year}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    if mailto or settings.openalex_mailto:
        params["mailto"] = mailto or settings.openalex_mailto
    if api_key or settings.openalex_api_key:
        params["api_key"] = api_key or settings.openalex_api_key

    url = f"{OPENALEX_BASE_URL}/works?{urllib.parse.urlencode(params)}"
    return _get_json(url)


def fetch_unpaywall(doi: str, email: str | None = None) -> dict[str, Any] | None:
    clean = normalize_doi(doi)
    mail = email or settings.unpaywall_email
    if not clean or not mail:
        return None
    url = f"{UNPAYWALL_BASE_URL}/{urllib.parse.quote(clean)}?{urllib.parse.urlencode({'email': mail})}"
    try:
        return _get_json(url)
    except RuntimeError:
        return None


def build_openalex_document(record: dict[str, Any]) -> SourceDocument:
    work = record.get("openalex", record)
    work_id = str(work.get("id") or work.get("doi") or stable_hash(json.dumps(work, sort_keys=True, default=str)))
    title = str(work.get("title") or work.get("display_name") or work_id)
    doi = normalize_doi(work.get("doi"))
    abstract = abstract_from_inverted_index(work.get("abstract_inverted_index"))
    text = _openalex_text(record, abstract)
    return SourceDocument(
        id=stable_hash(f"openalex:{work_id}"),
        path=f"openalex://{work_id}",
        source_type="openalex",
        title=title,
        text=text,
        metadata={
            "provider": "OpenAlex",
            "openalex_id": work_id,
            "doi": doi,
            "publication_year": work.get("publication_year"),
            "source_query": record.get("query"),
            "has_unpaywall": bool(record.get("unpaywall")),
        },
    )


def normalize_doi(value: Any) -> str | None:
    if not value:
        return None
    doi = str(value).strip()
    doi = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    doi = doi.removeprefix("doi:")
    return doi.lower() or None


def abstract_from_inverted_index(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for token, token_positions in index.items():
        if not isinstance(token_positions, list):
            continue
        for position in token_positions:
            if isinstance(position, int):
                positions.append((position, str(token)))
    return " ".join(token for _, token in sorted(positions))


def _openalex_text(record: dict[str, Any], abstract: str) -> str:
    work = record.get("openalex", record)
    unpaywall = record.get("unpaywall") or {}
    topics = [topic.get("display_name") for topic in work.get("topics") or [] if isinstance(topic, dict) and topic.get("display_name")]
    concepts = [concept.get("display_name") for concept in work.get("concepts") or [] if isinstance(concept, dict) and concept.get("display_name")]
    keywords = [item.get("display_name") or item.get("keyword") for item in work.get("keywords") or [] if isinstance(item, dict)]
    oa = work.get("open_access") or {}
    lines = [
        f"OpenAlex work: {work.get('id')}",
        f"Title: {work.get('title') or work.get('display_name')}",
        f"DOI: {normalize_doi(work.get('doi'))}",
        f"Publication year: {work.get('publication_year')}",
        f"Type: {work.get('type')}",
        f"Cited by count: {work.get('cited_by_count')}",
        f"Open access: {oa.get('is_oa') if isinstance(oa, dict) else oa}",
        f"OA URL: {_best_oa_url(work, unpaywall)}",
        f"Topics: {', '.join(topics[:12])}",
        f"Concepts: {', '.join(concepts[:20])}",
        f"Keywords: {', '.join(str(item) for item in keywords[:20] if item)}",
        "",
        "Abstract:",
        abstract,
        "",
        "Raw metadata JSON:",
        json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)[:16000],
    ]
    return "\n".join(lines)


def _best_oa_url(work: dict[str, Any], unpaywall: dict[str, Any]) -> str | None:
    if unpaywall.get("best_oa_location"):
        return unpaywall["best_oa_location"].get("url_for_pdf") or unpaywall["best_oa_location"].get("url")
    for key in ["best_oa_location", "primary_location"]:
        location = work.get(key)
        if isinstance(location, dict):
            return location.get("pdf_url") or location.get("landing_page_url")
    return None


def _get_json(url: str, retries: int = 4) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(url, headers={"User-Agent": "HypothesisFactory/0.1 (materials corpus research)"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                time.sleep(delay)
                continue
            if 500 <= exc.code < 600:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"Failed to fetch JSON from {url}: {last_error}")
