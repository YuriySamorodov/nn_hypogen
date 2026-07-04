from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.services.chunking import chunk_document
from backend.services.corpus_db import CorpusStore
from backend.services.materials_project import (
    build_materials_project_document,
    fetch_materials_project_page,
    parse_elements,
    parse_fields,
)


DEFAULT_OUTPUT_DIR = Path("/media/andy/XS2000/data_hack/materials_project")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Materials Project summary docs to JSONL and optionally ingest them into the corpus DB")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--chemsys", default=None, help="Chemical system query, for example Fe-Cr-Ni")
    parser.add_argument("--elements", default=None, help="Element filter, for example 'Fe Cr Ni' or 'Fe,Cr,Ni'")
    parser.add_argument("--all", action="store_true", help="Export all Materials Project summary docs")
    parser.add_argument("--yes-all", action="store_true", help="Required when --all is used without --limit")
    parser.add_argument("--fields", default=None, help="Comma-separated MP summary fields")
    parser.add_argument("--limit", type=int, default=1000, help="Maximum records to export; use 0 only with --all --yes-all")
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--ingest-db", action="store_true", help="Also persist exported docs into source_files/document_texts/chunks/structured_records")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--run-name", default=None)
    args = parser.parse_args()

    if not args.chemsys and not args.elements and not args.all:
        parser.error("set --chemsys, --elements, or --all")
    if args.all and args.limit == 0 and not args.yes_all:
        parser.error("--all with --limit 0 requires --yes-all")
    if args.chunk_size < 1:
        parser.error("--chunk-size must be positive")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "materials_project_summary.jsonl"
    manifest_path = output_dir / "manifest.json"

    seen_ids = _read_seen_ids(jsonl_path) if args.resume else set()
    fields = parse_fields(args.fields)
    max_records = None if args.limit == 0 else args.limit
    written = 0
    skipped_existing = 0
    started_at = datetime.utcnow().isoformat(timespec="seconds")

    store: CorpusStore | None = None
    run_id: str | None = None
    if args.ingest_db:
        store = CorpusStore(args.database_url)
        store.initialize_schema()
        run_id = store.create_run(
            output_dir,
            args.run_name or f"materials-project-export-{args.chemsys or args.elements or 'all'}",
            {
                "source": "materials_project",
                "output_dir": str(output_dir),
                "chemsys": args.chemsys,
                "elements": parse_elements(args.elements),
                "all": args.all,
                "fields": fields,
                "limit": max_records,
            },
        )
        store.update_run_status(run_id, "running")

    try:
        with jsonl_path.open("a", encoding="utf-8") as handle:
            page = 1
            while True:
                remaining = None if max_records is None else max_records - written
                if remaining is not None and remaining <= 0:
                    break
                page_size = min(args.chunk_size, remaining) if remaining is not None else args.chunk_size
                payloads = fetch_materials_project_page(
                    api_key=args.api_key,
                    chemsys=args.chemsys,
                    elements=parse_elements(args.elements),
                    fields=fields,
                    chunk_size=page_size,
                    page=page,
                    allow_all=args.all,
                )
                if not payloads:
                    break
                for payload in payloads:
                    material_id = str(payload.get("material_id") or "")
                    if material_id and material_id in seen_ids:
                        skipped_existing += 1
                        continue
                    handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                    seen_ids.add(material_id)
                    written += 1
                    if store and run_id:
                        _persist_payload(store, run_id, payload)
                    if max_records is not None and written >= max_records:
                        break
                handle.flush()
                _write_manifest(
                    manifest_path,
                    args=args,
                    fields=fields,
                    started_at=started_at,
                    written=written,
                    skipped_existing=skipped_existing,
                    run_id=run_id,
                    status="running",
                )
                page += 1
        if store and run_id:
            store.save_artifact(
                run_id,
                kind="materials_project",
                stage="materials_project_export",
                status="completed",
                path=str(jsonl_path),
                metadata={"records": written, "skipped_existing": skipped_existing, "fields": fields},
            )
            store.update_run_status(run_id, "completed")
        _write_manifest(
            manifest_path,
            args=args,
            fields=fields,
            started_at=started_at,
            written=written,
            skipped_existing=skipped_existing,
            run_id=run_id,
            status="completed",
        )
    except RuntimeError as exc:
        if store and run_id:
            store.save_artifact(run_id, kind="materials_project", stage="materials_project_export", status="failed", path=str(jsonl_path), metadata={"error": str(exc)})
            store.update_run_status(run_id, "completed_degraded")
        print(f"materials_project_export_error={exc}", file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()

    print(f"output={jsonl_path}")
    print(f"manifest={manifest_path}")
    print(f"records_written={written}")
    print(f"skipped_existing={skipped_existing}")
    if run_id:
        print(f"run_id={run_id}")
    return 0


def _persist_payload(store: CorpusStore, run_id: str, payload: dict[str, Any]) -> None:
    doc = build_materials_project_document(payload)
    material_id = str(payload.get("material_id") or doc.id)
    source_id = store.upsert_external_source(run_id, material_id, "materials_project", doc.title, payload)
    doc = store.save_document(run_id, source_id, doc, {"text_chars": len(doc.text), "ocr_required": False, "extractor": "mp-api"})
    store.replace_structured_records(run_id, source_id, {"materials_project_summary": [payload]})
    store.replace_chunks(run_id, source_id, chunk_document(doc, settings.chunk_size_chars, settings.chunk_overlap_chars))


def _read_seen_ids(jsonl_path: Path) -> set[str]:
    seen: set[str] = set()
    if not jsonl_path.exists():
        return seen
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            material_id = payload.get("material_id")
            if material_id:
                seen.add(str(material_id))
    return seen


def _write_manifest(
    manifest_path: Path,
    *,
    args: argparse.Namespace,
    fields: list[str],
    started_at: str,
    written: int,
    skipped_existing: int,
    run_id: str | None,
    status: str,
) -> None:
    payload = {
        "status": status,
        "started_at": started_at,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "provider": "Materials Project",
        "license": "CC BY 4.0",
        "api": "mp-api materials.summary.search",
        "query": {
            "chemsys": args.chemsys,
            "elements": parse_elements(args.elements),
            "all": args.all,
            "fields": fields,
            "limit": None if args.limit == 0 else args.limit,
            "chunk_size": args.chunk_size,
        },
        "records_written": written,
        "skipped_existing": skipped_existing,
        "run_id": run_id,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
