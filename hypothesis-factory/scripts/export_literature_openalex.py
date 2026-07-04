from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.services.chunking import chunk_document
from backend.services.corpus_db import CorpusStore
from backend.services.literature import (
    DEFAULT_MATERIALS_QUERY_PROFILES,
    build_openalex_document,
    dedupe_queries,
    fetch_openalex_works_page,
    fetch_unpaywall,
    get_materials_queries,
    get_materials_query_profile_categories,
    normalize_query_profile,
    normalize_doi,
)


DEFAULT_OUTPUT_DIR = Path("/media/andy/XS2000/data_hack/literature/openalex_materials")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export materials-science literature metadata from OpenAlex and optional Unpaywall OA metadata")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--query", action="append", default=[], help="OpenAlex search query; repeat for multiple queries")
    parser.add_argument("--queries-file", default=None, help="Text file with one query per line")
    parser.add_argument(
        "--profile",
        choices=["core", "adjacent", "mining", "energy", "bio-soft", "bio_soft", "computational", "full"],
        default=None,
        help="Built-in query profile for materials science and adjacent fields",
    )
    parser.add_argument("--list-profiles", action="store_true", help="Print built-in query profiles and exit")
    parser.add_argument("--default-queries", action="store_true", help="Backward-compatible alias for --profile full")
    parser.add_argument("--limit-per-query", type=int, default=1000, help="0 means no per-query limit; requires --yes-large")
    parser.add_argument("--limit-total", type=int, default=0, help="0 means no total limit")
    parser.add_argument("--yes-large", action="store_true", help="Required for unbounded or very large exports")
    parser.add_argument("--per-page", type=int, default=200)
    parser.add_argument("--from-year", type=int, default=None)
    parser.add_argument("--to-year", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--resume-from-jsonl", action="append", default=[], help="Additional JSONL files used only for resume/dedupe state")
    parser.add_argument("--sleep", type=float, default=0.05, help="Delay between OpenAlex pages")
    parser.add_argument("--progress-every", type=int, default=1000, help="Print progress every N written records; 0 disables progress logs")
    parser.add_argument("--page-timeout", type=int, default=90, help="Hard timeout in seconds for one OpenAlex page")
    parser.add_argument("--max-stale-pages", type=int, default=8, help="Stop a query after N pages that add no new records; 0 disables")
    parser.add_argument("--max-pages-per-query", type=int, default=0, help="Stop each query after N OpenAlex pages; 0 disables")
    parser.add_argument("--unpaywall", choices=["off", "auto", "always"], default="auto")
    parser.add_argument("--unpaywall-sleep", type=float, default=0.10)
    parser.add_argument("--ingest-db", action="store_true")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--run-name", default=None)
    args = parser.parse_args()

    if args.list_profiles:
        _print_profiles()
        return 0

    queries = _collect_queries(args)
    if not queries:
        parser.error("set --query, --queries-file, --profile, or --default-queries")
    if (args.limit_per_query == 0 or args.limit_total == 0 and len(queries) > 12) and not args.yes_large:
        parser.error("large/unbounded export requires --yes-large")
    if args.unpaywall == "always" and not settings.unpaywall_email:
        parser.error("--unpaywall always requires UNPAYWALL_EMAIL/openalex_mailto in .env")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "openalex_works.jsonl"
    manifest_path = output_dir / "manifest.json"

    seen_ids, resume_query_counts = _initial_resume_state(jsonl_path, args)
    started_at = datetime.utcnow().isoformat(timespec="seconds")
    written = 0
    skipped_existing = 0
    unpaywall_checked = 0
    per_query_counts: dict[str, int] = {}

    store: CorpusStore | None = None
    run_id: str | None = None
    if args.ingest_db:
        profile = _selected_profile(args)
        store = CorpusStore(args.database_url)
        store.initialize_schema()
        run_id = store.create_run(
            output_dir,
            args.run_name or "openalex-materials-literature",
            {
                "source": "openalex_unpaywall",
                "output_dir": str(output_dir),
                "profile": profile,
                "profile_categories": _selected_profile_categories(profile),
                "queries": queries,
                "limit_per_query": args.limit_per_query,
                "limit_total": args.limit_total,
                "unpaywall": args.unpaywall,
                "from_year": args.from_year,
                "to_year": args.to_year,
                "resume_from_jsonl": args.resume_from_jsonl,
            },
        )
        store.update_run_status(run_id, "running")

    try:
        with jsonl_path.open("a", encoding="utf-8") as handle:
            for query in queries:
                existing_for_query = resume_query_counts.get(query, 0)
                if args.limit_per_query and existing_for_query >= args.limit_per_query:
                    per_query_counts[query] = existing_for_query
                    print(
                        f"query_skipped={query!r} existing_records={existing_for_query} "
                        f"limit_per_query={args.limit_per_query} records_written={written}",
                        flush=True,
                    )
                    continue
                cursor = "*"
                query_written = existing_for_query
                query_new_written = 0
                query_pages = 0
                stale_pages = 0
                while True:
                    if args.limit_total and written >= args.limit_total:
                        break
                    if args.limit_per_query and query_written >= args.limit_per_query:
                        break
                    remaining_query = None if args.limit_per_query == 0 else args.limit_per_query - query_written
                    remaining_total = None if args.limit_total == 0 else args.limit_total - written
                    page_size = min(args.per_page, remaining_query or args.per_page, remaining_total or args.per_page)
                    if args.max_pages_per_query and query_pages >= args.max_pages_per_query:
                        print(
                            f"query_stopped={query!r} reason=max_pages_per_query "
                            f"pages={query_pages} query_written={query_written} records_written={written}",
                            flush=True,
                        )
                        break
                    try:
                        payload = _fetch_openalex_page_with_deadline(args, query, cursor, page_size)
                    except RuntimeError as exc:
                        print(
                            f"query_stopped={query!r} reason=fetch_error error={str(exc)!r} "
                            f"pages={query_pages} query_written={query_written} records_written={written}",
                            flush=True,
                        )
                        break
                    query_pages += 1
                    works = payload.get("results") or []
                    if not works:
                        break
                    page_new = 0
                    for work in works:
                        work_id = str(work.get("id") or work.get("doi") or "")
                        if not work_id:
                            continue
                        if work_id in seen_ids:
                            skipped_existing += 1
                            continue
                        record = {"query": query, "openalex": work, "unpaywall": None}
                        doi = normalize_doi(work.get("doi"))
                        if _should_check_unpaywall(args.unpaywall) and doi:
                            record["unpaywall"] = fetch_unpaywall(doi)
                            unpaywall_checked += 1
                            if args.unpaywall_sleep:
                                time.sleep(args.unpaywall_sleep)
                        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                        seen_ids.add(work_id)
                        written += 1
                        query_written += 1
                        query_new_written += 1
                        page_new += 1
                        _print_progress(args, query, query_written, written, skipped_existing, run_id)
                        if store and run_id:
                            _persist_record(store, run_id, record)
                    if page_new == 0:
                        stale_pages += 1
                    else:
                        stale_pages = 0
                    if args.max_stale_pages and stale_pages >= args.max_stale_pages:
                        print(
                            f"query_stopped={query!r} reason=max_stale_pages stale_pages={stale_pages} "
                            f"pages={query_pages} query_written={query_written} records_written={written}",
                            flush=True,
                        )
                        break
                    cursor = (payload.get("meta") or {}).get("next_cursor")
                    if not cursor:
                        break
                    if args.sleep:
                        time.sleep(args.sleep)
                    _write_manifest(
                        manifest_path,
                        args=args,
                        queries=queries,
                        started_at=started_at,
                        written=written,
                        skipped_existing=skipped_existing,
                        unpaywall_checked=unpaywall_checked,
                        per_query_counts=per_query_counts | {query: query_written},
                        run_id=run_id,
                        status="running",
                    )
                per_query_counts[query] = query_written
                print(
                    f"query_completed={query!r} query_written={query_written} "
                    f"query_new_written={query_new_written} records_written={written} skipped_existing={skipped_existing}",
                    flush=True,
                )
        if store and run_id:
            store.save_artifact(
                run_id,
                kind="openalex",
                stage="openalex_export",
                status="completed",
                path=str(jsonl_path),
                metadata={"records": written, "skipped_existing": skipped_existing, "unpaywall_checked": unpaywall_checked},
            )
            store.update_run_status(run_id, "completed")
        _write_manifest(
            manifest_path,
            args=args,
            queries=queries,
            started_at=started_at,
            written=written,
            skipped_existing=skipped_existing,
            unpaywall_checked=unpaywall_checked,
            per_query_counts=per_query_counts,
            run_id=run_id,
            status="completed",
        )
    except RuntimeError as exc:
        if store and run_id:
            store.save_artifact(run_id, kind="openalex", stage="openalex_export", status="failed", path=str(jsonl_path), metadata={"error": str(exc)})
            store.update_run_status(run_id, "completed_degraded")
        print(f"openalex_export_error={exc}", file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()

    print(f"output={jsonl_path}")
    print(f"manifest={manifest_path}")
    print(f"records_written={written}")
    print(f"skipped_existing={skipped_existing}")
    print(f"unpaywall_checked={unpaywall_checked}")
    if run_id:
        print(f"run_id={run_id}")
    return 0


def _collect_queries(args: argparse.Namespace) -> list[str]:
    queries: list[str] = []
    explicit_queries = list(args.query or [])
    if args.queries_file:
        path = Path(args.queries_file)
        explicit_queries.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#"))
    if explicit_queries:
        return dedupe_queries(explicit_queries)
    profile = _selected_profile(args)
    if profile:
        return get_materials_queries(profile)
    return []


def _selected_profile(args: argparse.Namespace) -> str | None:
    if args.query or args.queries_file:
        return None
    if args.profile:
        return normalize_query_profile(args.profile)
    if args.default_queries:
        return "full"
    return None


def _selected_profile_categories(profile: str | None) -> list[str]:
    if not profile:
        return []
    return get_materials_query_profile_categories(profile)


def _print_profiles() -> None:
    for profile, queries in DEFAULT_MATERIALS_QUERY_PROFILES.items():
        print(f"{profile}: {len(queries)} queries")


def _should_check_unpaywall(mode: str) -> bool:
    if mode == "off":
        return False
    if mode == "always":
        return True
    return bool(settings.unpaywall_email)


def _print_progress(
    args: argparse.Namespace,
    query: str,
    query_written: int,
    written: int,
    skipped_existing: int,
    run_id: str | None,
) -> None:
    interval = max(0, int(getattr(args, "progress_every", 0) or 0))
    if not interval or written % interval:
        return
    print(
        f"progress records_written={written} query={query!r} "
        f"query_written={query_written} skipped_existing={skipped_existing} run_id={run_id or ''}",
        flush=True,
    )


def _fetch_openalex_page_with_deadline(args: argparse.Namespace, query: str, cursor: str, page_size: int) -> dict[str, Any]:
    with _deadline(int(args.page_timeout or 0), f"OpenAlex page timed out for query={query!r}"):
        return fetch_openalex_works_page(
            query=query,
            cursor=cursor,
            per_page=page_size,
            from_year=args.from_year,
            to_year=args.to_year,
        )


@contextmanager
def _deadline(seconds: int, message: str):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _raise_timeout(_signum, _frame):
        raise RuntimeError(message)

    previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
    previous_alarm = signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_alarm:
            signal.alarm(previous_alarm)


def _persist_record(store: CorpusStore, run_id: str, record: dict[str, Any]) -> None:
    doc = build_openalex_document(record)
    work = record["openalex"]
    source_key = str(work.get("id") or work.get("doi") or doc.id).replace("https://openalex.org/", "")
    source_id = store.upsert_external_source(run_id, source_key, "openalex", doc.title, record)
    doc = store.save_document(run_id, source_id, doc, {"text_chars": len(doc.text), "ocr_required": False, "extractor": "openalex"})
    store.replace_structured_records(run_id, source_id, {"openalex_work": [record]})
    store.replace_chunks(run_id, source_id, chunk_document(doc, settings.chunk_size_chars, settings.chunk_overlap_chars))


def _initial_resume_state(jsonl_path: Path, args: argparse.Namespace) -> tuple[set[str], dict[str, int]]:
    paths: list[Path] = []
    if args.resume:
        paths.append(jsonl_path)
    paths.extend(Path(item) for item in args.resume_from_jsonl or [])
    return _read_resume_state(paths)


def _read_resume_state(jsonl_paths: list[Path]) -> tuple[set[str], dict[str, int]]:
    seen: set[str] = set()
    query_counts: dict[str, int] = {}
    for jsonl_path in jsonl_paths:
        if not jsonl_path.exists():
            continue
        with jsonl_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                work = record.get("openalex") or {}
                work_id = work.get("id") or work.get("doi")
                if work_id:
                    seen.add(str(work_id))
                query = record.get("query")
                if query:
                    query_counts[str(query)] = query_counts.get(str(query), 0) + 1
    return seen, query_counts


def _write_manifest(
    manifest_path: Path,
    *,
    args: argparse.Namespace,
    queries: list[str],
    started_at: str,
    written: int,
    skipped_existing: int,
    unpaywall_checked: int,
    per_query_counts: dict[str, int],
    run_id: str | None,
    status: str,
) -> None:
    profile = _selected_profile(args)
    payload = {
        "status": status,
        "started_at": started_at,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "provider": "OpenAlex + Unpaywall",
        "license_note": "OpenAlex metadata is open; Unpaywall returns legal OA locations when available.",
        "profile": profile,
        "profile_categories": _selected_profile_categories(profile),
        "queries": queries,
        "query_count": len(queries),
        "limit_per_query": None if args.limit_per_query == 0 else args.limit_per_query,
        "limit_total": None if args.limit_total == 0 else args.limit_total,
        "from_year": args.from_year,
        "to_year": args.to_year,
        "resume_from_jsonl": args.resume_from_jsonl,
        "unpaywall_mode": args.unpaywall,
        "unpaywall_enabled": _should_check_unpaywall(args.unpaywall),
        "records_written": written,
        "skipped_existing": skipped_existing,
        "unpaywall_checked": unpaywall_checked,
        "per_query_counts": per_query_counts,
        "run_id": run_id,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
