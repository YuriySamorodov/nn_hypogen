from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.config import settings
from backend.schemas import (
    DocumentChunk,
    Entity,
    ExtractabilityRecord,
    KnowledgeBase,
    Relation,
    SizeClassRecord,
    SourceDocument,
    TailingsSummary,
)


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return json.loads(value)


def stable_hash(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha1(value).hexdigest()[:16]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class CorpusStore:
    """Small DB adapter.

    PostgreSQL is used when CORPUS_DATABASE_URL is set. SQLite is a local-first
    fallback for tests and demos where external services are not available.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or settings.corpus_database_url
        self.driver = "postgres" if self.database_url and self.database_url.startswith(("postgres://", "postgresql://")) else "sqlite"
        self.sqlite_path: Path | None = None
        if self.driver == "postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("Install psycopg[binary] to use CORPUS_DATABASE_URL with PostgreSQL") from exc
            self.conn = psycopg.connect(self.database_url, row_factory=dict_row)
        else:
            sqlite_path = Path(self.database_url.removeprefix("sqlite:///")) if self.database_url and self.database_url.startswith("sqlite:///") else settings.corpus_sqlite_path
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self.sqlite_path = sqlite_path.resolve()
            self.conn = sqlite3.connect(sqlite_path)
            self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.driver == "postgres" else sql

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        self.conn.execute(self._sql(sql), tuple(params))
        self.conn.commit()

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(self._sql(sql), tuple(params)).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        rows = self.conn.execute(self._sql(sql), tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def initialize_schema(self) -> None:
        for statement in SCHEMA:
            if self.driver == "postgres" and "CREATE VIEW IF NOT EXISTS" in statement:
                statement = statement.replace("CREATE VIEW IF NOT EXISTS", "CREATE OR REPLACE VIEW")
            if self.driver == "postgres":
                statement = statement.replace("instr(", "strpos(")
            self.execute(statement)

    def create_run(self, root_path: Path, name: str | None = None, options: dict[str, Any] | None = None) -> str:
        root = str(root_path.resolve())
        run_id = stable_hash(f"{name or root}:{root}:{_now()}:{os.getpid()}")
        now = _now()
        self.execute(
            """
            INSERT INTO ingest_runs(id, name, root_path, status, options, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status, options=excluded.options, updated_at=excluded.updated_at
            """,
            (run_id, name or Path(root).name, root, "pending", _json(options or {}), now, now),
        )
        return run_id

    def latest_run_id(self) -> str:
        row = self.fetchone("SELECT id FROM ingest_runs ORDER BY created_at DESC LIMIT 1")
        if not row:
            raise RuntimeError("No corpus ingest runs found")
        return str(row["id"])

    def update_run_status(self, run_id: str, status: str) -> None:
        self.execute("UPDATE ingest_runs SET status=?, updated_at=? WHERE id=?", (status, _now(), run_id))

    def upsert_source_file(self, run_id: str, root_path: Path, path: Path) -> str:
        rel = str(path.relative_to(root_path))
        file_id = stable_hash(f"{run_id}:{rel}")
        now = _now()
        digest = file_sha256(path)
        self.execute(
            """
            INSERT INTO source_files(id, run_id, path, relative_path, source_type, sha256, bytes, status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              path=excluded.path, relative_path=excluded.relative_path, source_type=excluded.source_type,
              sha256=excluded.sha256, bytes=excluded.bytes, status=excluded.status,
              metadata=excluded.metadata, updated_at=excluded.updated_at
            """,
            (
                file_id,
                run_id,
                str(path),
                rel,
                _source_type_from_suffix(path),
                digest,
                path.stat().st_size,
                "queued",
                _json({}),
                now,
                now,
            ),
        )
        return file_id

    def upsert_external_source(
        self,
        run_id: str,
        source_key: str,
        source_type: str,
        title: str,
        payload: dict[str, Any],
    ) -> str:
        source_id = stable_hash(f"{run_id}:{source_type}:{source_key}")
        now = _now()
        payload_json = _json(payload)
        self.execute(
            """
            INSERT INTO source_files(id, run_id, path, relative_path, source_type, sha256, bytes, status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              path=excluded.path, relative_path=excluded.relative_path, source_type=excluded.source_type,
              sha256=excluded.sha256, bytes=excluded.bytes, status=excluded.status,
              metadata=excluded.metadata, updated_at=excluded.updated_at
            """,
            (
                source_id,
                run_id,
                f"{source_type}://{source_key}",
                f"{source_type}/{source_key}",
                source_type,
                hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                len(payload_json.encode("utf-8")),
                "extracted",
                _json({"title": title, "source_key": source_key, "external": True}),
                now,
                now,
            ),
        )
        return source_id

    def enqueue_job(self, run_id: str, stage: str, source_file_id: str | None = None, payload: dict[str, Any] | None = None) -> str:
        job_id = stable_hash(f"{run_id}:{stage}:{source_file_id or 'run'}:{_json(payload or {})}")
        now = _now()
        self.execute(
            """
            INSERT INTO ingest_jobs(id, run_id, source_file_id, stage, status, attempts, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
            """,
            (job_id, run_id, source_file_id, stage, "pending", 0, _json(payload or {}), now, now),
        )
        return job_id

    def next_pending_job(self) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM ingest_jobs WHERE status='pending' ORDER BY created_at, id LIMIT 1")

    def claim_next_job(self, run_id: str | None = None) -> dict[str, Any] | None:
        """Atomically move one pending ingest job to running and return it.

        This lets multiple worker processes share a run queue without processing
        the same source file twice.
        """
        now = _now()
        if self.driver == "postgres":
            run_filter = "AND run_id=?" if run_id else ""
            params: list[Any] = []
            if run_id:
                params.append(run_id)
            params.append(now)
            row = self.conn.execute(
                self._sql(
                    f"""
                    WITH next_job AS (
                      SELECT id
                      FROM ingest_jobs
                      WHERE status='pending' {run_filter}
                      ORDER BY created_at, id
                      LIMIT 1
                      FOR UPDATE SKIP LOCKED
                    )
                    UPDATE ingest_jobs
                    SET status='running', error=NULL, updated_at=?
                    WHERE id=(SELECT id FROM next_job)
                    RETURNING *
                    """
                ),
                tuple(params),
            ).fetchone()
            self.conn.commit()
            return dict(row) if row else None

        try:
            self.conn.execute("BEGIN IMMEDIATE")
            if run_id:
                row = self.conn.execute(
                    "SELECT * FROM ingest_jobs WHERE status='pending' AND run_id=? ORDER BY created_at, id LIMIT 1",
                    (run_id,),
                ).fetchone()
            else:
                row = self.conn.execute("SELECT * FROM ingest_jobs WHERE status='pending' ORDER BY created_at, id LIMIT 1").fetchone()
            if not row:
                self.conn.commit()
                return None
            self.conn.execute("UPDATE ingest_jobs SET status='running', error=NULL, updated_at=? WHERE id=?", (now, row["id"]))
            self.conn.commit()
            return dict(row)
        except Exception:
            self.conn.rollback()
            raise

    def mark_job(self, job_id: str, status: str, error: str | None = None) -> None:
        self.execute(
            "UPDATE ingest_jobs SET status=?, error=?, attempts=attempts+1, updated_at=? WHERE id=?",
            (status, error, _now(), job_id),
        )

    def save_document(self, run_id: str, source_file_id: str, doc: SourceDocument, text_quality: dict[str, Any]) -> SourceDocument:
        doc = doc.model_copy(update={"id": source_file_id, "metadata": {**doc.metadata, "run_id": run_id}})
        now = _now()
        self.execute(
            """
            INSERT INTO document_texts(id, run_id, source_file_id, title, text, text_quality, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              title=excluded.title, text=excluded.text, text_quality=excluded.text_quality,
              metadata=excluded.metadata, updated_at=excluded.updated_at
            """,
            (
                source_file_id,
                run_id,
                source_file_id,
                doc.title,
                doc.text,
                _json(text_quality),
                _json(doc.metadata),
                now,
                now,
            ),
        )
        self.execute(
            "UPDATE source_files SET status=?, metadata=?, updated_at=? WHERE id=?",
            ("extracted", _json(doc.metadata), now, source_file_id),
        )
        return doc

    def replace_chunks(self, run_id: str, source_file_id: str, chunks: list[DocumentChunk]) -> None:
        self.execute("DELETE FROM document_chunks WHERE run_id=? AND source_file_id=?", (run_id, source_file_id))
        now = _now()
        for chunk in chunks:
            self.execute(
                """
                INSERT INTO document_chunks(id, run_id, source_file_id, document_id, chunk_index, text, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.id,
                    run_id,
                    source_file_id,
                    chunk.document_id,
                    int(chunk.metadata.get("chunk_index", 0)),
                    chunk.text,
                    _json(chunk.metadata),
                    now,
                ),
            )

    def replace_structured_records(self, run_id: str, source_file_id: str, records: dict[str, list[object]]) -> None:
        self.execute("DELETE FROM structured_records WHERE run_id=? AND source_file_id=?", (run_id, source_file_id))
        self.append_structured_records(run_id, source_file_id, records)

    def append_structured_records(self, run_id: str, source_file_id: str, records: dict[str, list[object]]) -> None:
        idx = 0
        existing = self.fetchone(
            "SELECT COUNT(*) AS count FROM structured_records WHERE run_id=? AND source_file_id=?",
            (run_id, source_file_id),
        )
        offset = int(existing["count"]) if existing else 0
        for kind, items in records.items():
            for item in items:
                idx += 1
                payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                record_id = stable_hash(f"{run_id}:{source_file_id}:{kind}:{offset + idx}:{_json(payload)}")
                self.execute(
                    """
                    INSERT INTO structured_records(id, run_id, source_file_id, record_type, payload, provenance, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (record_id, run_id, source_file_id, kind, _json(payload), _json({"source_file_id": source_file_id}), _now()),
                )

    def replace_document_sections(self, run_id: str, source_file_id: str, sections: list[dict[str, Any]]) -> None:
        self.execute("DELETE FROM document_sections WHERE run_id=? AND source_file_id=?", (run_id, source_file_id))
        now = _now()
        for idx, section in enumerate(sections):
            section_id = str(section.get("id") or stable_hash(f"{run_id}:{source_file_id}:section:{idx}:{section.get('title', '')}:{section.get('text', '')[:200]}"))
            self.execute(
                """
                INSERT INTO document_sections(
                  id, run_id, source_file_id, section_index, section_type, title, text,
                  page_start, page_end, provenance, metadata, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  section_index=excluded.section_index, section_type=excluded.section_type,
                  title=excluded.title, text=excluded.text, page_start=excluded.page_start,
                  page_end=excluded.page_end, provenance=excluded.provenance,
                  metadata=excluded.metadata, updated_at=excluded.updated_at
                """,
                (
                    section_id,
                    run_id,
                    source_file_id,
                    int(section.get("section_index", idx)),
                    str(section.get("section_type") or "body"),
                    str(section.get("title") or ""),
                    str(section.get("text") or ""),
                    section.get("page_start"),
                    section.get("page_end"),
                    _json(section.get("provenance") or {"source_file_id": source_file_id}),
                    _json(section.get("metadata") or {}),
                    now,
                    now,
                ),
            )

    def replace_document_assets(self, run_id: str, source_file_id: str, assets: list[dict[str, Any]]) -> None:
        self.execute("DELETE FROM document_assets WHERE run_id=? AND source_file_id=?", (run_id, source_file_id))
        now = _now()
        for idx, asset in enumerate(assets):
            asset_id = str(asset.get("id") or stable_hash(f"{run_id}:{source_file_id}:asset:{idx}:{asset.get('asset_type', '')}:{asset.get('caption', '')}:{asset.get('content', '')[:200]}"))
            self.execute(
                """
                INSERT INTO document_assets(
                  id, run_id, source_file_id, asset_index, asset_type, label, caption,
                  content, path, page, provenance, metadata, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  asset_index=excluded.asset_index, asset_type=excluded.asset_type,
                  label=excluded.label, caption=excluded.caption, content=excluded.content,
                  path=excluded.path, page=excluded.page, provenance=excluded.provenance,
                  metadata=excluded.metadata, updated_at=excluded.updated_at
                """,
                (
                    asset_id,
                    run_id,
                    source_file_id,
                    int(asset.get("asset_index", idx)),
                    str(asset.get("asset_type") or "unknown"),
                    str(asset.get("label") or ""),
                    str(asset.get("caption") or ""),
                    str(asset.get("content") or ""),
                    asset.get("path"),
                    asset.get("page"),
                    _json(asset.get("provenance") or {"source_file_id": source_file_id}),
                    _json(asset.get("metadata") or {}),
                    now,
                    now,
                ),
            )

    def replace_kg_entities(self, run_id: str, entities: list[dict[str, Any]]) -> None:
        self.conn.execute(self._sql("DELETE FROM kg_entities WHERE run_id=?"), (run_id,))
        self.conn.execute(self._sql("DELETE FROM kg_entity_aliases WHERE run_id=?"), (run_id,))
        now = _now()
        for entity in entities:
            entity_id = str(entity.get("id") or stable_hash(f"{run_id}:{entity.get('entity_type')}:{entity.get('normalized') or entity.get('name')}"))
            aliases = [str(alias) for alias in entity.get("aliases", []) if str(alias).strip()]
            self.conn.execute(
                self._sql(
                    """
                INSERT INTO kg_entities(
                  id, run_id, entity_type, name, normalized, canonical_id, description,
                  confidence, source_count, first_source_file_id, metadata, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  entity_type=excluded.entity_type, name=excluded.name,
                  normalized=excluded.normalized, canonical_id=excluded.canonical_id,
                  description=excluded.description, confidence=excluded.confidence,
                  source_count=excluded.source_count, first_source_file_id=excluded.first_source_file_id,
                  metadata=excluded.metadata, updated_at=excluded.updated_at
                """
                ),
                (
                    entity_id,
                    run_id,
                    str(entity.get("entity_type") or "term"),
                    str(entity.get("name") or ""),
                    str(entity.get("normalized") or entity.get("name") or "").lower(),
                    entity.get("canonical_id"),
                    entity.get("description"),
                    float(entity.get("confidence", 0.7)),
                    int(entity.get("source_count", 1)),
                    entity.get("first_source_file_id"),
                    _json({**(entity.get("metadata") or {}), "aliases": aliases}),
                    now,
                    now,
                ),
            )
            for alias in aliases:
                alias_id = stable_hash(f"{run_id}:{entity_id}:alias:{alias.lower()}")
                self.conn.execute(
                    self._sql(
                        """
                    INSERT INTO kg_entity_aliases(id, run_id, entity_id, alias, normalized_alias, provenance, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET alias=excluded.alias, normalized_alias=excluded.normalized_alias, provenance=excluded.provenance
                    """
                    ),
                    (alias_id, run_id, entity_id, alias, alias.lower(), _json({"source": "kg_worker"}), now),
                )
        self.conn.commit()

    def replace_kg_relations(self, run_id: str, relations: list[dict[str, Any]]) -> None:
        self.conn.execute(self._sql("DELETE FROM kg_relations WHERE run_id=?"), (run_id,))
        now = _now()
        for relation in relations:
            rel_id = str(
                relation.get("id")
                or stable_hash(
                    f"{run_id}:{relation.get('subject_id') or relation.get('subject')}:{relation.get('predicate')}:{relation.get('object_id') or relation.get('object_value')}:{relation.get('evidence_chunk_id') or relation.get('source_file_id')}"
                )
            )
            self.conn.execute(
                self._sql(
                    """
                INSERT INTO kg_relations(
                  id, run_id, subject_entity_id, predicate, object_entity_id, object_value,
                  evidence_text, evidence_chunk_id, source_file_id, confidence, extractor,
                  provenance, metadata, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  subject_entity_id=excluded.subject_entity_id, predicate=excluded.predicate,
                  object_entity_id=excluded.object_entity_id, object_value=excluded.object_value,
                  evidence_text=excluded.evidence_text, evidence_chunk_id=excluded.evidence_chunk_id,
                  source_file_id=excluded.source_file_id, confidence=excluded.confidence,
                  extractor=excluded.extractor, provenance=excluded.provenance,
                  metadata=excluded.metadata, updated_at=excluded.updated_at
                """
                ),
                (
                    rel_id,
                    run_id,
                    relation.get("subject_entity_id"),
                    str(relation.get("predicate") or "related_to"),
                    relation.get("object_entity_id"),
                    relation.get("object_value"),
                    str(relation.get("evidence_text") or ""),
                    relation.get("evidence_chunk_id"),
                    relation.get("source_file_id"),
                    float(relation.get("confidence", 0.6)),
                    str(relation.get("extractor") or "kg_worker"),
                    _json(relation.get("provenance") or {}),
                    _json(relation.get("metadata") or {}),
                    now,
                    now,
                ),
            )
        self.conn.commit()

    def replace_kg_embeddings(self, run_id: str, embeddings: list[dict[str, Any]], target_type: str | None = None) -> None:
        if target_type:
            self.conn.execute(self._sql("DELETE FROM kg_embeddings WHERE run_id=? AND target_type=?"), (run_id, target_type))
        else:
            self.conn.execute(self._sql("DELETE FROM kg_embeddings WHERE run_id=?"), (run_id,))
        now = _now()
        for item in embeddings:
            emb_id = str(item.get("id") or stable_hash(f"{run_id}:{item.get('target_type')}:{item.get('target_id')}:{item.get('model')}"))
            vector = item.get("embedding") or []
            self.conn.execute(
                self._sql(
                    """
                INSERT INTO kg_embeddings(
                  id, run_id, target_type, target_id, model, dimensions, embedding,
                  payload, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  model=excluded.model, dimensions=excluded.dimensions, embedding=excluded.embedding,
                  payload=excluded.payload, updated_at=excluded.updated_at
                """
                ),
                (
                    emb_id,
                    run_id,
                    str(item.get("target_type") or "unknown"),
                    str(item.get("target_id") or ""),
                    str(item.get("model") or "unknown"),
                    int(item.get("dimensions") or len(vector)),
                    _json(vector),
                    _json(item.get("payload") or {}),
                    now,
                    now,
                ),
            )
        self.conn.commit()

    def upsert_kg_sync_status(self, run_id: str, target: str, status: str, counts: dict[str, Any] | None = None, error: str | None = None) -> None:
        sync_id = stable_hash(f"{run_id}:{target}")
        now = _now()
        self.execute(
            """
            INSERT INTO kg_sync_status(id, run_id, target, status, counts, error, updated_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              status=excluded.status, counts=excluded.counts, error=excluded.error, updated_at=excluded.updated_at
            """,
            (sync_id, run_id, target, status, _json(counts or {}), error, now, now),
        )

    def save_artifact(
        self,
        run_id: str,
        kind: str,
        stage: str,
        status: str,
        source_file_id: str | None = None,
        path: str | None = None,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        artifact_id = stable_hash(f"{run_id}:{source_file_id or 'run'}:{kind}:{stage}:{path or content or _now()}")
        self.execute(
            """
            INSERT INTO artifacts(id, run_id, source_file_id, kind, stage, status, path, content, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status, path=excluded.path, content=excluded.content, metadata=excluded.metadata
            """,
            (artifact_id, run_id, source_file_id, kind, stage, status, path, content, _json(metadata or {}), _now()),
        )
        return artifact_id

    def log_llm_call(self, run_id: str, provider: str, model: str, purpose: str, status: str, prompt_chars: int, response_chars: int = 0, error: str | None = None) -> None:
        call_id = stable_hash(f"{run_id}:{provider}:{model}:{purpose}:{_now()}")
        self.execute(
            """
            INSERT INTO llm_calls(id, run_id, provider, model, purpose, status, prompt_chars, response_chars, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (call_id, run_id, provider, model, purpose, status, prompt_chars, response_chars, error, _now()),
        )

    def replace_entities_relations(self, run_id: str, entities: list[Entity], relations: list[Relation]) -> None:
        self.execute("DELETE FROM entities WHERE run_id=?", (run_id,))
        self.execute("DELETE FROM relations WHERE run_id=?", (run_id,))
        for entity in entities:
            self.execute(
                "INSERT INTO entities(id, run_id, entity_type, name, normalized, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"{run_id}:{entity.id}", run_id, entity.type, entity.name, entity.normalized, _json(entity.metadata), _now()),
            )
        for idx, relation in enumerate(relations):
            self.execute(
                """
                INSERT INTO relations(id, run_id, source, relation, target, evidence_text, source_ref, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{run_id}:relation:{idx}",
                    run_id,
                    relation.source,
                    relation.relation,
                    relation.target,
                    relation.evidence_text,
                    relation.source_ref.model_dump_json(),
                    relation.confidence,
                    _now(),
                ),
            )

    def run_status(self, run_id: str) -> dict[str, Any]:
        run = self.fetchone("SELECT * FROM ingest_runs WHERE id=?", (run_id,))
        if not run:
            raise RuntimeError(f"Run not found: {run_id}")
        counts: dict[str, Any] = {"run": run}
        for table in [
            "source_files",
            "document_texts",
            "document_chunks",
            "structured_records",
            "artifacts",
            "llm_calls",
            "entities",
            "relations",
            "document_sections",
            "document_assets",
            "kg_entities",
            "kg_relations",
            "kg_embeddings",
        ]:
            counts[table] = self.fetchone(f"SELECT COUNT(*) AS count FROM {table} WHERE run_id=?", (run_id,))["count"]
        counts["jobs"] = self.fetchall("SELECT stage, status, COUNT(*) AS count FROM ingest_jobs WHERE run_id=? GROUP BY stage, status ORDER BY stage, status", (run_id,))
        counts["kg_sync_status"] = self.fetchall("SELECT target, status, counts, error, updated_at FROM kg_sync_status WHERE run_id=? ORDER BY target", (run_id,))
        return counts


def load_knowledge_base_from_db(run_id: str = "latest", database_url: str | None = None) -> KnowledgeBase:
    store = CorpusStore(database_url)
    store.initialize_schema()
    try:
        actual_run_id = store.latest_run_id() if run_id == "latest" else run_id
        kb = KnowledgeBase()
        for row in store.fetchall(
            """
            SELECT sf.id, sf.path, sf.source_type, dt.title, dt.text, dt.metadata
            FROM source_files sf
            JOIN document_texts dt ON dt.source_file_id=sf.id
            WHERE sf.run_id=?
            ORDER BY sf.relative_path
            """,
            (actual_run_id,),
        ):
            kb.source_documents.append(
                SourceDocument(
                    id=row["id"],
                    path=row["path"],
                    source_type=row["source_type"],
                    title=row["title"],
                    text=row["text"] or "",
                    metadata=_loads(row["metadata"], {}),
                )
            )
        for row in store.fetchall("SELECT * FROM document_chunks WHERE run_id=? ORDER BY source_file_id, chunk_index", (actual_run_id,)):
            kb.chunks.append(DocumentChunk(id=row["id"], document_id=row["document_id"], text=row["text"], metadata=_loads(row["metadata"], {})))
        for row in store.fetchall("SELECT record_type, payload FROM structured_records WHERE run_id=? ORDER BY record_type, id", (actual_run_id,)):
            payload = _loads(row["payload"], {})
            if row["record_type"] == "summaries":
                kb.summaries.append(TailingsSummary.model_validate(payload))
            elif row["record_type"] == "size_classes":
                kb.size_classes.append(SizeClassRecord.model_validate(payload))
            elif row["record_type"] == "extractability":
                kb.extractability.append(ExtractabilityRecord.model_validate(payload))
        return kb
    finally:
        store.close()


def _source_type_from_suffix(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in {".txt", ".md"}:
        return "txt"
    if ext == ".xlsx":
        return "xlsx"
    if ext in {".png", ".jpg", ".jpeg"}:
        return "image"
    return "unknown"


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS ingest_runs (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      root_path TEXT NOT NULL,
      status TEXT NOT NULL,
      options TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingest_jobs (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      source_file_id TEXT,
      stage TEXT NOT NULL,
      status TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,
      payload TEXT NOT NULL,
      error TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_files (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      path TEXT NOT NULL,
      relative_path TEXT NOT NULL,
      source_type TEXT NOT NULL,
      sha256 TEXT NOT NULL,
      bytes INTEGER NOT NULL,
      status TEXT NOT NULL,
      metadata TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_texts (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      source_file_id TEXT NOT NULL,
      title TEXT NOT NULL,
      text TEXT NOT NULL,
      text_quality TEXT NOT NULL,
      metadata TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_chunks (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      source_file_id TEXT NOT NULL,
      document_id TEXT NOT NULL,
      chunk_index INTEGER NOT NULL,
      text TEXT NOT NULL,
      metadata TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS structured_records (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      source_file_id TEXT NOT NULL,
      record_type TEXT NOT NULL,
      payload TEXT NOT NULL,
      provenance TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entities (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      entity_type TEXT NOT NULL,
      name TEXT NOT NULL,
      normalized TEXT,
      metadata TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS relations (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      source TEXT NOT NULL,
      relation TEXT NOT NULL,
      target TEXT NOT NULL,
      evidence_text TEXT NOT NULL,
      source_ref TEXT NOT NULL,
      confidence REAL NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      source_file_id TEXT,
      kind TEXT NOT NULL,
      stage TEXT NOT NULL,
      status TEXT NOT NULL,
      path TEXT,
      content TEXT,
      metadata TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_calls (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      provider TEXT NOT NULL,
      model TEXT NOT NULL,
      purpose TEXT NOT NULL,
      status TEXT NOT NULL,
      prompt_chars INTEGER NOT NULL,
      response_chars INTEGER NOT NULL,
      error TEXT,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_sections (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      source_file_id TEXT NOT NULL,
      section_index INTEGER NOT NULL,
      section_type TEXT NOT NULL,
      title TEXT NOT NULL,
      text TEXT NOT NULL,
      page_start INTEGER,
      page_end INTEGER,
      provenance TEXT NOT NULL,
      metadata TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_assets (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      source_file_id TEXT NOT NULL,
      asset_index INTEGER NOT NULL,
      asset_type TEXT NOT NULL,
      label TEXT NOT NULL,
      caption TEXT NOT NULL,
      content TEXT NOT NULL,
      path TEXT,
      page INTEGER,
      provenance TEXT NOT NULL,
      metadata TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kg_entities (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      entity_type TEXT NOT NULL,
      name TEXT NOT NULL,
      normalized TEXT NOT NULL,
      canonical_id TEXT,
      description TEXT,
      confidence REAL NOT NULL,
      source_count INTEGER NOT NULL,
      first_source_file_id TEXT,
      metadata TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kg_entity_aliases (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      entity_id TEXT NOT NULL,
      alias TEXT NOT NULL,
      normalized_alias TEXT NOT NULL,
      provenance TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kg_relations (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      subject_entity_id TEXT,
      predicate TEXT NOT NULL,
      object_entity_id TEXT,
      object_value TEXT,
      evidence_text TEXT NOT NULL,
      evidence_chunk_id TEXT,
      source_file_id TEXT,
      confidence REAL NOT NULL,
      extractor TEXT NOT NULL,
      provenance TEXT NOT NULL,
      metadata TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kg_embeddings (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      target_type TEXT NOT NULL,
      target_id TEXT NOT NULL,
      model TEXT NOT NULL,
      dimensions INTEGER NOT NULL,
      embedding TEXT NOT NULL,
      payload TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kg_sync_status (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      target TEXT NOT NULL,
      status TEXT NOT NULL,
      counts TEXT NOT NULL,
      error TEXT,
      updated_at TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ingest_runs_created_at ON ingest_runs(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_ingest_jobs_run_status ON ingest_jobs(run_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_source_files_run_type ON source_files(run_id, source_type)",
    "CREATE INDEX IF NOT EXISTS idx_document_texts_run_source ON document_texts(run_id, source_file_id)",
    "CREATE INDEX IF NOT EXISTS idx_document_chunks_run_source ON document_chunks(run_id, source_file_id, chunk_index)",
    "CREATE INDEX IF NOT EXISTS idx_structured_records_run_type ON structured_records(run_id, record_type)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_run_stage_status ON artifacts(run_id, stage, status)",
    "CREATE INDEX IF NOT EXISTS idx_entities_run_type ON entities(run_id, entity_type)",
    "CREATE INDEX IF NOT EXISTS idx_relations_run_relation ON relations(run_id, relation)",
    "CREATE INDEX IF NOT EXISTS idx_document_sections_run_source ON document_sections(run_id, source_file_id, section_index)",
    "CREATE INDEX IF NOT EXISTS idx_document_sections_run_type ON document_sections(run_id, section_type)",
    "CREATE INDEX IF NOT EXISTS idx_document_assets_run_type ON document_assets(run_id, asset_type)",
    "CREATE INDEX IF NOT EXISTS idx_kg_entities_run_type ON kg_entities(run_id, entity_type)",
    "CREATE INDEX IF NOT EXISTS idx_kg_entities_run_normalized ON kg_entities(run_id, normalized)",
    "CREATE INDEX IF NOT EXISTS idx_kg_aliases_run_alias ON kg_entity_aliases(run_id, normalized_alias)",
    "CREATE INDEX IF NOT EXISTS idx_kg_relations_run_predicate ON kg_relations(run_id, predicate)",
    "CREATE INDEX IF NOT EXISTS idx_kg_relations_run_subject ON kg_relations(run_id, subject_entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_kg_relations_run_object ON kg_relations(run_id, object_entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_kg_embeddings_run_target ON kg_embeddings(run_id, target_type, target_id)",
    "CREATE INDEX IF NOT EXISTS idx_kg_sync_status_run_target ON kg_sync_status(run_id, target)",
    """
    CREATE VIEW IF NOT EXISTS corpus_document_overview AS
    SELECT
      sf.run_id,
      sf.id AS source_file_id,
      sf.relative_path,
      sf.source_type,
      sf.bytes,
      sf.sha256,
      sf.status AS source_status,
      dt.title,
      LENGTH(dt.text) AS text_chars,
      dt.text_quality,
      dt.metadata,
      COALESCE(ch.chunk_count, 0) AS chunk_count
    FROM source_files sf
    LEFT JOIN document_texts dt ON dt.source_file_id = sf.id
    LEFT JOIN (
      SELECT run_id, source_file_id, COUNT(*) AS chunk_count
      FROM document_chunks
      GROUP BY run_id, source_file_id
    ) ch ON ch.run_id = sf.run_id AND ch.source_file_id = sf.id
    """,
    """
    CREATE VIEW IF NOT EXISTS corpus_run_summary AS
    SELECT
      r.id AS run_id,
      r.name,
      r.root_path,
      r.status,
      r.created_at,
      COALESCE(sf.count, 0) AS source_files,
      COALESCE(dt.count, 0) AS documents,
      COALESCE(dc.count, 0) AS chunks,
      COALESCE(sr.count, 0) AS structured_records,
      COALESCE(e.count, 0) AS entities,
      COALESCE(rel.count, 0) AS relations,
      COALESCE(ds.count, 0) AS document_sections,
      COALESCE(da.count, 0) AS document_assets,
      COALESCE(kge.count, 0) AS kg_entities,
      COALESCE(kgr.count, 0) AS kg_relations,
      COALESCE(kgem.count, 0) AS kg_embeddings
    FROM ingest_runs r
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM source_files GROUP BY run_id) sf ON sf.run_id = r.id
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM document_texts GROUP BY run_id) dt ON dt.run_id = r.id
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM document_chunks GROUP BY run_id) dc ON dc.run_id = r.id
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM structured_records GROUP BY run_id) sr ON sr.run_id = r.id
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM entities GROUP BY run_id) e ON e.run_id = r.id
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM relations GROUP BY run_id) rel ON rel.run_id = r.id
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM document_sections GROUP BY run_id) ds ON ds.run_id = r.id
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM document_assets GROUP BY run_id) da ON da.run_id = r.id
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM kg_entities GROUP BY run_id) kge ON kge.run_id = r.id
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM kg_relations GROUP BY run_id) kgr ON kgr.run_id = r.id
    LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM kg_embeddings GROUP BY run_id) kgem ON kgem.run_id = r.id
    """,
    """
    CREATE VIEW IF NOT EXISTS corpus_quality_issues AS
    SELECT
      sf.run_id,
      sf.id AS source_file_id,
      sf.relative_path,
      sf.source_type,
      dt.text_quality,
      dt.metadata
    FROM source_files sf
    JOIN document_texts dt ON dt.source_file_id = sf.id
    WHERE (instr(dt.text_quality, 'ocr_required') > 0 AND instr(dt.text_quality, 'true') > 0)
      OR LENGTH(dt.text) < 200
    """,
]
