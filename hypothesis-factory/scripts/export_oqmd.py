from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.services.chunking import chunk_document
from backend.services.corpus_db import CorpusStore
from backend.services.oqmd import (
    build_oqmd_document,
    fetch_oqmd_page,
    oqmd_source_key,
    parse_oqmd_fields,
)


DEFAULT_OUTPUT_DIR = Path("/media/andy/XS2000/data_hack/oqmd")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export OQMD formationenergy records to JSONL and optionally ingest into the corpus DB")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--all", action="store_true", help="Export all public formationenergy records")
    parser.add_argument("--yes-all", action="store_true", help="Required when --all is used with --limit 0")
    parser.add_argument("--limit", type=int, default=1000, help="Maximum total records including resumed records; 0 means no limit")
    parser.add_argument("--page-size", type=int, default=settings.oqmd_page_size)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--fields", default=None, help="Comma-separated OQMD fields")
    parser.add_argument("--filter", dest="filter_value", default=None, help="OQMD REST filter expression, for example element_set=(Al-Fe),O")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--sleep", type=float, default=settings.oqmd_sleep_seconds)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--continue-on-error", action="store_true", help="Bisect failing pages and skip single bad offsets instead of stopping")
    parser.add_argument("--min-page-size", type=int, default=1, help="Smallest page size used while bisecting failed pages")
    parser.add_argument("--max-consecutive-skips", type=int, default=50, help="Stop if this many single offsets are skipped in a row")
    parser.add_argument("--ingest-db", action="store_true")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--run-name", default=None)
    args = parser.parse_args()

    if not args.all and not args.filter_value:
        parser.error("set --all or --filter")
    if args.all and args.limit == 0 and not args.yes_all:
        parser.error("--all with --limit 0 requires --yes-all")
    if args.page_size < 1:
        parser.error("--page-size must be positive")
    if args.offset < 0:
        parser.error("--offset must be non-negative")
    if args.min_page_size < 1:
        parser.error("--min-page-size must be positive")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "oqmd_formationenergy.jsonl"
    manifest_path = output_dir / "manifest.json"
    fields = parse_oqmd_fields(args.fields)
    max_records = None if args.limit == 0 else args.limit
    started_at = datetime.utcnow().isoformat(timespec="seconds")

    seen_keys = _read_seen_keys(jsonl_path) if args.resume else set()
    existing_records = len(seen_keys)
    written = 0
    skipped_existing = 0
    failed_pages: list[dict[str, Any]] = []
    current_offset = args.offset + existing_records if args.resume and args.offset == 0 else args.offset

    store: CorpusStore | None = None
    run_id: str | None = None
    if args.ingest_db:
        store = CorpusStore(args.database_url)
        store.initialize_schema()
        run_id = store.create_run(
            output_dir,
            args.run_name or "oqmd-formationenergy-export",
            {
                "source": "oqmd",
                "output_dir": str(output_dir),
                "all": args.all,
                "filter": args.filter_value,
                "fields": fields,
                "limit": max_records,
                "page_size": args.page_size,
                "offset": args.offset,
                "resume": args.resume,
            },
        )
        store.update_run_status(run_id, "running")

    status = "completed"
    consecutive_skips = 0
    try:
        with jsonl_path.open("a", encoding="utf-8") as handle:
            while True:
                total_available = existing_records + written
                if max_records is not None and total_available >= max_records:
                    break
                page_size = args.page_size
                if max_records is not None:
                    page_size = min(page_size, max_records - total_available)
                payloads, skipped_offsets = _fetch_payloads_with_recovery(
                    limit=page_size,
                    offset=current_offset,
                    fields=fields,
                    filter_value=args.filter_value,
                    timeout=args.timeout,
                    continue_on_error=args.continue_on_error,
                    min_page_size=args.min_page_size,
                    failed_pages=failed_pages,
                )
                if payloads is None:
                    status = "completed_degraded"
                    break
                if skipped_offsets:
                    status = "completed_degraded"
                    consecutive_skips += skipped_offsets
                else:
                    consecutive_skips = 0
                if consecutive_skips >= args.max_consecutive_skips:
                    status = "completed_degraded"
                    failed_pages.append(
                        {
                            "offset": current_offset,
                            "limit": page_size,
                            "error": f"stopped after {consecutive_skips} consecutive skipped offsets",
                        }
                    )
                    break
                if not payloads and not skipped_offsets:
                    break
                for payload in payloads:
                    if not isinstance(payload, dict):
                        continue
                    source_key = oqmd_source_key(payload)
                    if source_key in seen_keys:
                        skipped_existing += 1
                        continue
                    handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                    seen_keys.add(source_key)
                    written += 1
                    if store and run_id:
                        _persist_payload(store, run_id, payload)
                handle.flush()
                current_offset += len(payloads) + skipped_offsets
                _write_manifest(
                    manifest_path,
                    args=args,
                    fields=fields,
                    started_at=started_at,
                    existing_records=existing_records,
                    written=written,
                    skipped_existing=skipped_existing,
                    current_offset=current_offset,
                    failed_pages=failed_pages,
                    run_id=run_id,
                    status="running",
                )
                if len(payloads) + skipped_offsets < page_size:
                    break
                if args.sleep:
                    time.sleep(args.sleep)
    finally:
        if store and run_id:
            artifact_status = "completed" if status == "completed" else "failed"
            store.save_artifact(
                run_id,
                kind="oqmd",
                stage="oqmd_export",
                status=artifact_status,
                path=str(jsonl_path),
                metadata={
                    "records_written": written,
                    "existing_records": existing_records,
                    "skipped_existing": skipped_existing,
                    "failed_pages": failed_pages,
                    "fields": fields,
                    "filter": args.filter_value,
                },
            )
            store.update_run_status(run_id, status)
            store.close()

    _write_manifest(
        manifest_path,
        args=args,
        fields=fields,
        started_at=started_at,
        existing_records=existing_records,
        written=written,
        skipped_existing=skipped_existing,
        current_offset=current_offset,
        failed_pages=failed_pages,
        run_id=run_id,
        status=status,
    )
    print(f"output={jsonl_path}")
    print(f"manifest={manifest_path}")
    print(f"records_written={written}")
    print(f"existing_records={existing_records}")
    print(f"skipped_existing={skipped_existing}")
    print(f"current_offset={current_offset}")
    if run_id:
        print(f"run_id={run_id}")
    return 0 if status == "completed" else 2


def _persist_payload(store: CorpusStore, run_id: str, payload: dict[str, Any]) -> None:
    doc = build_oqmd_document(payload)
    source_key = oqmd_source_key(payload)
    source_id = store.upsert_external_source(run_id, source_key, "oqmd", doc.title, payload)
    doc = store.save_document(run_id, source_id, doc, {"text_chars": len(doc.text), "ocr_required": False, "extractor": "oqmd-rest"})
    store.replace_structured_records(run_id, source_id, {"oqmd_formationenergy": [payload]})
    store.replace_chunks(run_id, source_id, chunk_document(doc, settings.chunk_size_chars, settings.chunk_overlap_chars))


def _fetch_payloads_with_recovery(
    *,
    limit: int,
    offset: int,
    fields: list[str],
    filter_value: str | None,
    timeout: int,
    continue_on_error: bool,
    min_page_size: int,
    failed_pages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]] | None, int]:
    try:
        page = fetch_oqmd_page(limit=limit, offset=offset, fields=fields, filter_value=filter_value, timeout=timeout)
        return [payload for payload in (page.get("data") or []) if isinstance(payload, dict)], 0
    except RuntimeError as exc:
        failed_pages.append({"offset": offset, "limit": limit, "error": str(exc)})
        print(f"oqmd_page_error offset={offset} limit={limit} error={exc}", file=sys.stderr, flush=True)
        if not continue_on_error:
            return None, 0
        if limit <= min_page_size:
            print(f"oqmd_offset_skipped offset={offset} error={exc}", file=sys.stderr, flush=True)
            return [], 1

    first_limit = max(min_page_size, limit // 2)
    second_limit = limit - first_limit
    first_payloads, first_skips = _fetch_payloads_with_recovery(
        limit=first_limit,
        offset=offset,
        fields=fields,
        filter_value=filter_value,
        timeout=timeout,
        continue_on_error=True,
        min_page_size=min_page_size,
        failed_pages=failed_pages,
    )
    if first_payloads is None:
        first_payloads = []
    if second_limit <= 0:
        return first_payloads, first_skips
    second_payloads, second_skips = _fetch_payloads_with_recovery(
        limit=second_limit,
        offset=offset + first_limit,
        fields=fields,
        filter_value=filter_value,
        timeout=timeout,
        continue_on_error=True,
        min_page_size=min_page_size,
        failed_pages=failed_pages,
    )
    if second_payloads is None:
        second_payloads = []
    return first_payloads + second_payloads, first_skips + second_skips


def _read_seen_keys(jsonl_path: Path) -> set[str]:
    seen: set[str] = set()
    if not jsonl_path.exists():
        return seen
    with jsonl_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                seen.add(oqmd_source_key(payload))
    return seen


def _write_manifest(
    manifest_path: Path,
    *,
    args: argparse.Namespace,
    fields: list[str],
    started_at: str,
    existing_records: int,
    written: int,
    skipped_existing: int,
    current_offset: int,
    failed_pages: list[dict[str, Any]],
    run_id: str | None,
    status: str,
) -> None:
    payload = {
        "status": status,
        "started_at": started_at,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "provider": "OQMD",
        "license": "CC BY 4.0",
        "api": "OQMD REST formationenergy",
        "base_url": settings.oqmd_base_url,
        "query": {
            "all": args.all,
            "filter": args.filter_value,
            "fields": fields,
            "limit": None if args.limit == 0 else args.limit,
            "page_size": args.page_size,
            "initial_offset": args.offset,
            "current_offset": current_offset,
        },
        "existing_records": existing_records,
        "records_written": written,
        "total_records_in_jsonl": existing_records + written,
        "skipped_existing": skipped_existing,
        "failed_pages": failed_pages,
        "run_id": run_id,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
