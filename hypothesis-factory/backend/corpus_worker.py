from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.services.chunking import chunk_document
from backend.services.corpus_db import CorpusStore, load_knowledge_base_from_db
from backend.services.entity_extraction import extract_entities
from backend.services.ingestion import parse_source_file
from backend.services.materials_project import (
    build_materials_project_document,
    fetch_materials_project_summaries,
    parse_elements,
    parse_fields,
)
from backend.services.openalex import (
    DEFAULT_SEARCH,
    DEFAULT_TOPICS,
    build_openalex_document,
    fetch_openalex_works,
)
from backend.services.pdf_converter import extract_formula_records, extract_table_records
from backend.services.pdf_ocr import needs_deepseek_ocr_assist, ocr_pdf_with_tesseract
from backend.services.relation_extraction import extract_relations


Mode = str


def ingest_command(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    if not root.exists():
        raise FileNotFoundError(root)

    store = CorpusStore(args.database_url)
    store.initialize_schema()
    options = {"ocr": args.ocr, "repomix": args.repomix, "deepseek": args.deepseek}
    run_id = store.create_run(root, args.run_name, options)
    store.update_run_status(run_id, "running")

    file_count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if store.sqlite_path and path.resolve() == store.sqlite_path:
            continue
        source_file_id = store.upsert_source_file(run_id, root, path)
        store.enqueue_job(run_id, "extract", source_file_id, {"root_path": str(root)})
        file_count += 1

    processed = process_jobs(store, run_id=run_id, once=False, ocr=args.ocr, deepseek=args.deepseek)
    if args.repomix != "off":
        store.enqueue_job(run_id, "repomix_pack", None, {"root_path": str(root), "mode": args.repomix})
    if args.deepseek != "off":
        store.enqueue_job(run_id, "deepseek_structure", None, {"mode": args.deepseek})
    processed += process_jobs(store, run_id=run_id, once=False, ocr=args.ocr, deepseek=args.deepseek, repomix=args.repomix)
    promote_run(store, run_id)

    status = store.run_status(run_id)
    store.close()
    print(f"run_id={run_id}")
    print(f"files={file_count}")
    print(f"processed_jobs={processed}")
    print_status(status)
    return 0


def worker_command(args: argparse.Namespace) -> int:
    store = CorpusStore(args.database_url)
    store.initialize_schema()
    processed = process_jobs(store, run_id=args.run_id, once=args.once, ocr=args.ocr, deepseek=args.deepseek, repomix=args.repomix)
    if not args.once and args.run_id:
        promote_run(store, args.run_id)
    store.close()
    print(f"processed_jobs={processed}")
    return 0


def status_command(args: argparse.Namespace) -> int:
    store = CorpusStore(args.database_url)
    store.initialize_schema()
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    status = store.run_status(run_id)
    store.close()
    print_status(status)
    return 0


def materials_project_command(args: argparse.Namespace) -> int:
    store = CorpusStore(args.database_url)
    store.initialize_schema()
    fields = parse_fields(args.fields)
    try:
        payloads = fetch_materials_project_summaries(
            api_key=args.api_key,
            chemsys=args.chemsys,
            elements=parse_elements(args.elements),
            fields=fields,
            limit=args.limit,
            allow_all=args.all,
        )
    except RuntimeError as exc:
        store.close()
        print(f"materials_project_error={exc}", file=sys.stderr)
        return 2
    root = settings.project_root / "external" / "materials_project"
    run_name = args.run_name or f"materials-project-{args.chemsys or args.elements or 'query'}"
    run_id = store.create_run(
        root,
        run_name,
        {
            "source": "materials_project",
            "chemsys": args.chemsys,
            "elements": parse_elements(args.elements),
            "fields": fields,
            "limit": args.limit,
            "all": args.all,
        },
    )
    store.update_run_status(run_id, "running")

    for payload in payloads:
        doc = build_materials_project_document(payload)
        material_id = str(payload.get("material_id") or doc.id)
        source_id = store.upsert_external_source(run_id, material_id, "materials_project", doc.title, payload)
        doc = store.save_document(
            run_id,
            source_id,
            doc,
            {"text_chars": len(doc.text), "ocr_required": False, "extractor": "mp-api"},
        )
        store.replace_structured_records(run_id, source_id, {"materials_project_summary": [payload]})
        chunks = chunk_document(doc, settings.chunk_size_chars, settings.chunk_overlap_chars)
        store.replace_chunks(run_id, source_id, chunks)

    store.save_artifact(
        run_id,
        kind="materials_project",
        stage="materials_project_fetch",
        status="completed",
        metadata={"records": len(payloads), "fields": fields, "chemsys": args.chemsys, "elements": parse_elements(args.elements)},
    )
    promote_run(store, run_id)
    status = store.run_status(run_id)
    store.close()
    print(f"run_id={run_id}")
    print(f"materials_project_records={len(payloads)}")
    print_status(status)
    return 0


def openalex_command(args: argparse.Namespace) -> int:
    store = CorpusStore(args.database_url)
    store.initialize_schema()
    topic_ids = [item.strip() for item in (args.topics or "").split(",") if item.strip()] or None
    try:
        payloads = fetch_openalex_works(
            search=args.search,
            topic_ids=topic_ids,
            year_from=args.year_from,
            year_to=args.year_to,
            per_page=args.per_page,
            page=args.page,
            limit=args.limit,
            mailto=args.mailto,
        )
    except Exception as exc:
        store.close()
        print(f"openalex_error={exc}", file=sys.stderr)
        return 2

    root = settings.project_root / "external" / "openalex"
    run_name = args.run_name or f"openalex-materials-{args.year_from}-{args.year_to}"
    run_id = store.create_run(
        root,
        run_name,
        {
            "source": "openalex",
            "search": args.search,
            "topics": topic_ids or DEFAULT_TOPICS,
            "year_from": args.year_from,
            "year_to": args.year_to,
            "limit": args.limit,
        },
    )
    store.update_run_status(run_id, "running")

    for payload in payloads:
        doc = build_openalex_document(payload)
        external_id = str(payload.get("openalex_id") or doc.id).rsplit("/", 1)[-1]
        source_id = store.upsert_external_source(run_id, external_id, "openalex", doc.title, payload)
        store.save_document(
            run_id,
            source_id,
            doc,
            {"text_chars": len(doc.text), "ocr_required": False, "extractor": "openalex-api"},
        )
        store.replace_structured_records(run_id, source_id, {"openalex_work": [payload]})
        chunks = chunk_document(doc, settings.chunk_size_chars, settings.chunk_overlap_chars)
        store.replace_chunks(run_id, source_id, chunks)

    store.save_artifact(
        run_id,
        kind="openalex",
        stage="openalex_fetch",
        status="completed",
        metadata={"records": len(payloads), "search": args.search, "topics": topic_ids or DEFAULT_TOPICS},
    )
    promote_run(store, run_id)
    status = store.run_status(run_id)
    store.close()
    print(f"run_id={run_id}")
    print(f"openalex_records={len(payloads)}")
    print_status(status)
    return 0


def process_jobs(
    store: CorpusStore,
    run_id: str | None = None,
    once: bool = False,
    ocr: Mode = "auto",
    deepseek: Mode = "auto",
    repomix: Mode = "auto",
) -> int:
    processed = 0
    while True:
        job = _next_job(store, run_id)
        if not job:
            return processed
        try:
            stage = job["stage"]
            if stage == "extract":
                process_extract_job(store, job, ocr)
            elif stage == "ocr":
                process_ocr_job(store, job, ocr, deepseek)
            elif stage == "chunk_index":
                process_chunk_job(store, job)
            elif stage == "repomix_pack":
                process_repomix_job(store, job, repomix)
            elif stage == "deepseek_structure":
                process_deepseek_job(store, job, deepseek)
            else:
                raise RuntimeError(f"Unknown job stage: {stage}")
            store.mark_job(job["id"], "completed")
        except Exception as exc:
            store.mark_job(job["id"], "failed", str(exc))
        processed += 1
        if once:
            return processed


def _next_job(store: CorpusStore, run_id: str | None) -> dict[str, Any] | None:
    if run_id:
        return store.fetchone(
            "SELECT * FROM ingest_jobs WHERE status='pending' AND run_id=? ORDER BY created_at, id LIMIT 1",
            (run_id,),
        )
    return store.next_pending_job()


def process_extract_job(store: CorpusStore, job: dict[str, Any], ocr: Mode) -> None:
    payload = json.loads(job["payload"])
    source = store.fetchone("SELECT * FROM source_files WHERE id=?", (job["source_file_id"],))
    if not source:
        raise RuntimeError(f"Missing source file for job {job['id']}")

    root = Path(payload["root_path"])
    path = Path(source["path"])
    doc, structured = parse_source_file(path, root)
    quality = {
        "text_chars": len(doc.text or ""),
        "ocr_required": bool(doc.metadata.get("ocr_required")),
        "extractor": doc.metadata.get("parser", doc.source_type),
    }
    doc = store.save_document(job["run_id"], source["id"], doc, quality)
    store.replace_structured_records(job["run_id"], source["id"], structured)

    if doc.metadata.get("ocr_required") and ocr != "off":
        store.enqueue_job(job["run_id"], "ocr", source["id"], {"mode": ocr})
    else:
        store.enqueue_job(job["run_id"], "chunk_index", source["id"], {})


def process_ocr_job(store: CorpusStore, job: dict[str, Any], ocr: Mode, deepseek: Mode = "auto") -> None:
    source = store.fetchone("SELECT * FROM source_files WHERE id=?", (job["source_file_id"],))
    doc = store.fetchone("SELECT * FROM document_texts WHERE source_file_id=?", (job["source_file_id"],))
    if not source or not doc:
        raise RuntimeError(f"Missing OCR source/document for job {job['id']}")

    source_type = source["source_type"]
    path = Path(source["path"])
    if source_type == "pdf":
        process_pdf_ocr_job(store, job, source, doc, ocr, deepseek)
        return

    if source_type != "image":
        store.save_artifact(
            job["run_id"],
            kind="ocr",
            stage="ocr",
            status="skipped",
            source_file_id=source["id"],
            metadata={"reason": "unsupported_ocr_source_type", "source_type": source_type, "mode": ocr},
        )
        store.enqueue_job(job["run_id"], "chunk_index", source["id"], {})
        return

    tesseract = shutil.which("tesseract")
    if not tesseract:
        store.save_artifact(
            job["run_id"],
            kind="ocr",
            stage="ocr",
            status="skipped",
            source_file_id=source["id"],
            metadata={"reason": "tesseract_not_found", "mode": ocr},
        )
        store.enqueue_job(job["run_id"], "chunk_index", source["id"], {})
        return

    completed = subprocess.run(
        [tesseract, str(path), "stdout", "-l", "rus+eng"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        store.save_artifact(
            job["run_id"],
            kind="ocr",
            stage="ocr",
            status="failed",
            source_file_id=source["id"],
            metadata={"returncode": completed.returncode, "stderr": completed.stderr[:1000]},
        )
        store.enqueue_job(job["run_id"], "chunk_index", source["id"], {})
        return

    text = completed.stdout.strip()
    _update_ocr_document(store, job["run_id"], source, doc, text, {"ocr_engine": "tesseract", "ocr_chars": len(text)}, "tesseract")
    store.save_artifact(job["run_id"], kind="ocr", stage="ocr", status="completed", source_file_id=source["id"], content=text[:4000])
    store.enqueue_job(job["run_id"], "chunk_index", source["id"], {})


def process_pdf_ocr_job(
    store: CorpusStore,
    job: dict[str, Any],
    source: dict[str, Any],
    doc: dict[str, Any],
    ocr: Mode,
    deepseek: Mode,
) -> None:
    path = Path(source["path"])
    result = ocr_pdf_with_tesseract(
        path,
        max_pages=settings.pdf_ocr_max_pages,
        dpi=settings.pdf_ocr_dpi,
        languages=settings.pdf_ocr_languages,
    )
    store.save_artifact(
        job["run_id"],
        kind="ocr",
        stage="ocr",
        status=result.status,
        source_file_id=source["id"],
        content=result.text[:4000] if result.text else None,
        metadata={**result.metadata, "mode": ocr, "source_type": "pdf"},
    )

    final_text = result.text
    final_metadata = dict(result.metadata)
    final_extractor = str(final_metadata.get("ocr_engine") or "pdf_ocr")
    structured: dict[str, list[dict[str, Any]]] = {}

    if deepseek != "off" and needs_deepseek_ocr_assist(
        result,
        min_chars=settings.pdf_ocr_min_chars,
        quality_threshold=settings.pdf_ocr_quality_threshold,
    ):
        improved = _deepseek_improve_ocr_text(store, job["run_id"], source["id"], path, result.text, result.metadata)
        if improved:
            final_text = improved["text"]
            final_metadata.update(improved["metadata"])
            final_extractor = "deepseek_ocr_cleanup"
            structured.update(improved["structured"])

    if final_text.strip():
        structured.setdefault("pdf_ocr_formulas", extract_formula_records(final_text))
        structured.setdefault("pdf_ocr_tables", extract_table_records(final_text))
        _update_ocr_document(store, job["run_id"], source, doc, final_text, final_metadata, final_extractor)
        store.append_structured_records(job["run_id"], source["id"], structured)
    else:
        metadata = json.loads(doc["metadata"])
        metadata.update({"ocr_required": True, "ocr_status": result.status, "ocr_metadata": final_metadata})
        store.execute(
            """
            UPDATE document_texts
            SET metadata=?, text_quality=?, updated_at=?
            WHERE source_file_id=?
            """,
            (
                json.dumps(metadata, ensure_ascii=False),
                json.dumps({"text_chars": len(doc["text"] or ""), "ocr_required": True, "extractor": final_extractor}, ensure_ascii=False),
                _timestamp(),
                source["id"],
            ),
        )
    store.enqueue_job(job["run_id"], "chunk_index", source["id"], {})


def _update_ocr_document(
    store: CorpusStore,
    run_id: str,
    source: dict[str, Any],
    doc: dict[str, Any],
    ocr_text: str,
    ocr_metadata: dict[str, Any],
    extractor: str,
) -> None:
    existing_text = (doc["text"] or "").strip()
    text = _merge_ocr_text(existing_text, ocr_text)
    metadata = json.loads(doc["metadata"])
    metadata.update(
        {
            "ocr_required": False,
            "ocr_status": "completed",
            "ocr_chars": len(ocr_text),
            "ocr_metadata": ocr_metadata,
        }
    )
    store.execute(
        """
        UPDATE document_texts
        SET text=?, metadata=?, text_quality=?, updated_at=?
        WHERE source_file_id=?
        """,
        (
            text,
            json.dumps(metadata, ensure_ascii=False),
            json.dumps({"text_chars": len(text), "ocr_required": False, "extractor": extractor}, ensure_ascii=False),
            _timestamp(),
            source["id"],
        ),
    )


def _merge_ocr_text(existing_text: str, ocr_text: str) -> str:
    if not existing_text or len(existing_text) < 200:
        return ocr_text.strip()
    return f"{existing_text}\n\nExtracted OCR fallback text:\n{ocr_text.strip()}"


def _deepseek_improve_ocr_text(
    store: CorpusStore,
    run_id: str,
    source_file_id: str,
    path: Path,
    local_text: str,
    local_metadata: dict[str, Any],
) -> dict[str, Any] | None:
    purpose = "pdf_ocr_cleanup"
    model = settings.deepseek_model_fast
    if not settings.deepseek_api_key:
        store.log_llm_call(run_id, "deepseek", model, purpose, "skipped", 0, 0, "DEEPSEEK_API_KEY is not set")
        store.save_artifact(
            run_id,
            kind="deepseek_ocr",
            stage="ocr_fallback",
            status="skipped",
            source_file_id=source_file_id,
            metadata={"reason": "missing_api_key", "model": model, "local_ocr": local_metadata},
        )
        return None
    if not local_text.strip():
        store.log_llm_call(run_id, "deepseek", model, purpose, "skipped", 0, 0, "local OCR text is empty")
        store.save_artifact(
            run_id,
            kind="deepseek_ocr",
            stage="ocr_fallback",
            status="skipped",
            source_file_id=source_file_id,
            metadata={"reason": "empty_local_ocr_text", "model": model, "local_ocr": local_metadata},
        )
        return None

    prompt = (
        "You are cleaning OCR from a materials-science or metallurgy PDF. "
        "Repair obvious OCR artifacts, preserve formulas, chemical symbols, units, page markers and table-like rows. "
        "Return valid JSON with keys: text, formulas, tables, quality_notes. "
        "Do not invent missing content.\n\n"
        f"File: {path.name}\n"
        f"Local OCR metadata: {json.dumps(local_metadata, ensure_ascii=False)[:2000]}\n\n"
        f"OCR text:\n{local_text[:24000]}"
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "Return valid JSON only. Preserve technical evidence and uncertainty."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.deepseek_base_url}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = json.loads(response.read().decode("utf-8"))
        response_text = raw["choices"][0]["message"]["content"]
        payload = json.loads(response_text)
        improved_text = str(payload.get("text") or "").strip()
        if not improved_text:
            raise ValueError("DeepSeek response did not include non-empty text")
        store.log_llm_call(run_id, "deepseek", model, purpose, "completed", len(prompt), len(response_text))
        store.save_artifact(
            run_id,
            kind="deepseek_ocr",
            stage="ocr_fallback",
            status="completed",
            source_file_id=source_file_id,
            content=response_text[:4000],
            metadata={"model": model, "response_chars": len(response_text)},
        )
        return {
            "text": improved_text,
            "metadata": {"deepseek_ocr_cleanup": True, "deepseek_model": model},
            "structured": {"deepseek_ocr_structure": [payload]},
        }
    except (urllib.error.URLError, KeyError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        store.log_llm_call(run_id, "deepseek", model, purpose, "failed", len(prompt), 0, str(exc))
        store.save_artifact(
            run_id,
            kind="deepseek_ocr",
            stage="ocr_fallback",
            status="failed",
            source_file_id=source_file_id,
            metadata={"error": str(exc), "model": model},
        )
        return None


def process_chunk_job(store: CorpusStore, job: dict[str, Any]) -> None:
    source = store.fetchone("SELECT * FROM source_files WHERE id=?", (job["source_file_id"],))
    doc_row = store.fetchone("SELECT * FROM document_texts WHERE source_file_id=?", (job["source_file_id"],))
    if not source or not doc_row:
        raise RuntimeError(f"Missing chunk source/document for job {job['id']}")

    doc = {
        "id": source["id"],
        "path": source["path"],
        "source_type": source["source_type"],
        "title": doc_row["title"],
        "text": doc_row["text"],
        "metadata": json.loads(doc_row["metadata"]),
    }
    from backend.schemas import SourceDocument

    source_doc = SourceDocument.model_validate(doc)
    chunks = chunk_document(source_doc, settings.chunk_size_chars, settings.chunk_overlap_chars)
    store.replace_chunks(job["run_id"], source["id"], chunks)


def process_repomix_job(store: CorpusStore, job: dict[str, Any], repomix: Mode) -> None:
    payload = json.loads(job["payload"])
    root = Path(payload["root_path"])
    binary = shutil.which("repomix")
    if not binary:
        store.save_artifact(job["run_id"], kind="repomix", stage="repomix_pack", status="skipped", metadata={"reason": "repomix_not_found", "mode": repomix})
        return

    output_dir = settings.corpus_artifacts_dir / job["run_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "repomix-output.md"
    completed = subprocess.run([binary, str(root), "--output", str(output)], check=False, capture_output=True, text=True, timeout=180)
    if completed.returncode != 0:
        store.save_artifact(
            job["run_id"],
            kind="repomix",
            stage="repomix_pack",
            status="failed",
            path=str(output),
            metadata={"returncode": completed.returncode, "stderr": completed.stderr[:2000]},
        )
        return
    store.save_artifact(job["run_id"], kind="repomix", stage="repomix_pack", status="completed", path=str(output), metadata={"stdout": completed.stdout[:1000]})


def process_deepseek_job(store: CorpusStore, job: dict[str, Any], deepseek: Mode) -> None:
    if not settings.deepseek_api_key:
        store.log_llm_call(job["run_id"], "deepseek", settings.deepseek_model_struct, "corpus_structure", "skipped", 0, 0, "DEEPSEEK_API_KEY is not set")
        store.save_artifact(job["run_id"], kind="deepseek", stage="deepseek_structure", status="skipped", metadata={"reason": "missing_api_key", "mode": deepseek})
        return

    kb = load_knowledge_base_from_db(job["run_id"], store.database_url)
    corpus_text = "\n\n".join(chunk.text for chunk in kb.chunks[:30])
    prompt = (
        "Extract a compact JSON summary for a flotation tailings hypothesis corpus. "
        "Return keys: topics, process_nodes, equipment, unresolved_ocr, data_quality_notes.\n\n"
        f"{corpus_text[:24000]}"
    )
    body = json.dumps(
        {
            "model": settings.deepseek_model_struct,
            "messages": [
                {"role": "system", "content": "You structure technical metallurgy evidence. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.deepseek_base_url}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = json.loads(response.read().decode("utf-8"))
        text = raw["choices"][0]["message"]["content"]
        store.log_llm_call(job["run_id"], "deepseek", settings.deepseek_model_struct, "corpus_structure", "completed", len(prompt), len(text))
        store.save_artifact(job["run_id"], kind="deepseek", stage="deepseek_structure", status="completed", content=text, metadata={"model": settings.deepseek_model_struct})
    except (urllib.error.URLError, KeyError, TimeoutError, json.JSONDecodeError) as exc:
        store.log_llm_call(job["run_id"], "deepseek", settings.deepseek_model_struct, "corpus_structure", "failed", len(prompt), 0, str(exc))
        store.save_artifact(job["run_id"], kind="deepseek", stage="deepseek_structure", status="failed", metadata={"error": str(exc)})


def promote_run(store: CorpusStore, run_id: str) -> None:
    kb = load_knowledge_base_from_db(run_id, store.database_url)
    entities = extract_entities(kb)
    relations = extract_relations(kb)
    store.replace_entities_relations(run_id, entities, relations)

    failed_jobs = store.fetchone("SELECT COUNT(*) AS count FROM ingest_jobs WHERE run_id=? AND status='failed'", (run_id,))["count"]
    skipped_artifacts = store.fetchone("SELECT COUNT(*) AS count FROM artifacts WHERE run_id=? AND status IN ('skipped', 'failed')", (run_id,))["count"]
    status = "completed_degraded" if failed_jobs or skipped_artifacts else "completed"
    store.update_run_status(run_id, status)


def print_status(status: dict[str, Any]) -> None:
    run = status["run"]
    print(f"status={run['status']}")
    print(f"run_id={run['id']}")
    print(f"name={run['name']}")
    for key in [
        "source_files",
        "document_texts",
        "document_chunks",
        "structured_records",
        "entities",
        "relations",
        "document_sections",
        "document_assets",
        "kg_entities",
        "kg_relations",
        "kg_embeddings",
        "artifacts",
        "llm_calls",
    ]:
        print(f"{key}={status[key]}")
    for row in status["jobs"]:
        print(f"job {row['stage']} {row['status']}={row['count']}")
    for row in status.get("kg_sync_status", []):
        print(f"kg_sync {row['target']} {row['status']} counts={row['counts']} error={row['error']}")


def _timestamp() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Corpus ingestion worker for Hypothesis Factory")
    parser.add_argument("--database-url", default=None, help="PostgreSQL URL or sqlite:/// path; defaults to CORPUS_DATABASE_URL or local corpus.db")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Create an ingest run and process the source folder")
    ingest.add_argument("--path", required=True)
    ingest.add_argument("--run-name", default=None)
    ingest.add_argument("--ocr", choices=["auto", "always", "off"], default="auto")
    ingest.add_argument("--repomix", choices=["auto", "always", "off"], default="auto")
    ingest.add_argument("--deepseek", choices=["auto", "always", "off"], default="auto")
    ingest.set_defaults(func=ingest_command)

    worker = sub.add_parser("worker", help="Process pending ingest jobs")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--run-id", default=None)
    worker.add_argument("--ocr", choices=["auto", "always", "off"], default="auto")
    worker.add_argument("--repomix", choices=["auto", "always", "off"], default="auto")
    worker.add_argument("--deepseek", choices=["auto", "always", "off"], default="auto")
    worker.set_defaults(func=worker_command)

    status = sub.add_parser("status", help="Show ingest run status")
    status.add_argument("--run-id", default="latest")
    status.set_defaults(func=status_command)

    mp = sub.add_parser("materials-project", help="Ingest Materials Project summary docs through mp-api")
    mp.add_argument("--chemsys", default=None, help="Chemical system query, for example Fe-Cr-Ni")
    mp.add_argument("--elements", default=None, help="Element filter, for example 'Fe Cr Ni' or 'Fe,Cr,Ni'")
    mp.add_argument("--all", action="store_true", help="Query all Materials Project summary docs; use with a small --limit for smoke tests")
    mp.add_argument("--fields", default=None, help="Comma-separated MP summary fields")
    mp.add_argument("--limit", type=int, default=100)
    mp.add_argument("--run-name", default=None)
    mp.add_argument("--api-key", default=None, help="Overrides MP_API_KEY/MATERIALS_PROJECT_API_KEY")
    mp.set_defaults(func=materials_project_command)

    oa = sub.add_parser("openalex", help="Ingest materials-science article metadata from OpenAlex")
    oa.add_argument("--search", default=None, help="Optional full-text search; omit to use topic filters only")
    oa.add_argument("--topics", default=",".join(DEFAULT_TOPICS), help="Comma-separated OpenAlex topic ids")
    oa.add_argument("--year-from", type=int, default=2015)
    oa.add_argument("--year-to", type=int, default=2024)
    oa.add_argument("--per-page", type=int, default=50)
    oa.add_argument("--page", type=int, default=1)
    oa.add_argument("--limit", type=int, default=100)
    oa.add_argument("--run-name", default=None)
    oa.add_argument("--mailto", default=None, help="Contact email for OpenAlex polite pool")
    oa.set_defaults(func=openalex_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
