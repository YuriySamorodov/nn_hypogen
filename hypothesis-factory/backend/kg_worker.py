from __future__ import annotations

import argparse
import sys
from typing import Any

from backend.services.corpus_db import CorpusStore
from backend.services.materials_kg import (
    build_document_layers,
    build_embeddings,
    build_entities,
    build_relations,
    load_materials_kg_context,
    sync_neo4j,
    sync_qdrant,
)


DEFAULT_STAGES = ["sections", "assets", "entities", "relations", "embeddings", "sync"]


def build_command(args: argparse.Namespace) -> int:
    store = CorpusStore(args.database_url)
    store.initialize_schema()
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    stages = _parse_stages(args.stages)
    try:
        result = build_run(store, run_id, stages, args.grobid, args.neo4j, args.qdrant)
        status = store.run_status(run_id)
    finally:
        store.close()
    print_build_result(result)
    print_status(status)
    if args.qdrant == "required" and result.get("qdrant", {}).get("status") != "completed":
        return 2
    return 0


def build_all_command(args: argparse.Namespace) -> int:
    store = CorpusStore(args.database_url)
    store.initialize_schema()
    stages = _parse_stages(args.stages)
    rows = runs_with_chunks(store)
    if args.limit_runs:
        rows = rows[: args.limit_runs]
    print(f"runs={len(rows)}")
    failures = 0
    try:
        for row in rows:
            run_id = str(row["run_id"])
            print(
                "run "
                f"{run_id} name={row['name']} status={row['status']} "
                f"documents={row['documents']} chunks={row['chunks']} embeddings={row['kg_embeddings']}"
            )
            try:
                result = build_run(store, run_id, stages, args.grobid, args.neo4j, args.qdrant)
                print_build_result(result)
                if args.qdrant == "required" and result.get("qdrant", {}).get("status") != "completed":
                    failures += 1
            except Exception as exc:
                failures += 1
                store.upsert_kg_sync_status(run_id, "kg_worker", "failed", error=str(exc))
                print(f"run_error {run_id}: {exc}", file=sys.stderr)
    finally:
        store.close()
    print(f"completed_runs={len(rows) - failures}")
    print(f"failed_runs={failures}")
    return 2 if failures and args.fail_fast else 0


def build_run(
    store: CorpusStore,
    run_id: str,
    stages: list[str],
    grobid: str = "auto",
    neo4j: str = "auto",
    qdrant: str = "auto",
) -> dict[str, Any]:
    result: dict[str, Any] = {"run_id": run_id, "stages": stages}
    if "sections" in stages or "assets" in stages:
        result["document_layers"] = build_document_layers(store, run_id, grobid=grobid)
    entities = None
    if "entities" in stages:
        entities = build_entities(store, run_id)
        result["entities"] = len(entities)
    if "relations" in stages:
        relations = build_relations(store, run_id, entities=entities)
        result["relations"] = len(relations)
    if "embeddings" in stages:
        embeddings = build_embeddings(store, run_id)
        result["embeddings"] = len(embeddings)
    if "sync" in stages:
        if neo4j != "off":
            result["neo4j"] = sync_neo4j(store, run_id)
        else:
            store.upsert_kg_sync_status(run_id, "neo4j", "skipped", error="disabled by --neo4j off")
            result["neo4j"] = {"status": "skipped", "error": "disabled by --neo4j off"}
        if qdrant != "off":
            result["qdrant"] = sync_qdrant(store, run_id)
        else:
            store.upsert_kg_sync_status(run_id, "qdrant", "skipped", error="disabled by --qdrant off")
            result["qdrant"] = {"status": "skipped", "error": "disabled by --qdrant off"}
    store.upsert_kg_sync_status(run_id, "kg_worker", "completed", counts=_result_counts(result))
    return result


def runs_with_chunks(store: CorpusStore) -> list[dict[str, Any]]:
    return store.fetchall(
        """
        SELECT
          r.id AS run_id,
          r.name,
          r.status,
          COALESCE(dt.count, 0) AS documents,
          COALESCE(dc.count, 0) AS chunks,
          COALESCE(kgem.count, 0) AS kg_embeddings
        FROM ingest_runs r
        LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM document_texts GROUP BY run_id) dt ON dt.run_id = r.id
        LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM document_chunks GROUP BY run_id) dc ON dc.run_id = r.id
        LEFT JOIN (SELECT run_id, COUNT(*) AS count FROM kg_embeddings GROUP BY run_id) kgem ON kgem.run_id = r.id
        WHERE COALESCE(dc.count, 0) > 0
        ORDER BY r.created_at
        """
    )


def status_command(args: argparse.Namespace) -> int:
    store = CorpusStore(args.database_url)
    store.initialize_schema()
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    status = store.run_status(run_id)
    store.close()
    print_status(status)
    return 0


def search_command(args: argparse.Namespace) -> int:
    context = load_materials_kg_context(args.run_id, args.query, args.database_url, args.top_k)
    print(f"run_id={context['run_id']}")
    print(f"query={context['query']}")
    print(f"evidence={len(context['evidence'])}")
    for idx, evidence in enumerate(context["evidence"], 1):
        print(f"\n[{idx}] relevance={evidence.relevance:.3f} source={evidence.source.source_id} section={evidence.source.section}")
        print(evidence.text.replace("\n", " ")[:700])
    print(f"\ngraph_hits={len(context['graph_hits'])}")
    for idx, hit in enumerate(context["graph_hits"], 1):
        subject = hit.get("subject_name") or hit.get("subject_entity_id") or "document"
        obj = hit.get("object_name") or hit.get("object_value") or hit.get("object_entity_id")
        print(f"[g{idx}] {subject} --{hit.get('predicate')}--> {obj} confidence={hit.get('confidence')}")
    return 0


def _parse_stages(value: str) -> list[str]:
    stages = [stage.strip() for stage in value.split(",") if stage.strip()]
    if not stages:
        return DEFAULT_STAGES
    allowed = set(DEFAULT_STAGES)
    unknown = sorted(set(stages) - allowed)
    if unknown:
        raise ValueError(f"Unknown KG stages: {', '.join(unknown)}")
    return stages


def print_build_result(result: dict[str, Any]) -> None:
    print(f"run_id={result['run_id']}")
    print(f"stages={','.join(result['stages'])}")
    for key in ["document_layers", "entities", "relations", "embeddings", "neo4j", "qdrant"]:
        if key in result:
            print(f"{key}={result[key]}")


def _result_counts(result: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    for key in ["document_layers", "entities", "relations", "embeddings", "neo4j", "qdrant"]:
        if key in result:
            counts[key] = result[key]
    return counts


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
        "document_sections",
        "document_assets",
        "kg_entities",
        "kg_relations",
        "kg_embeddings",
        "artifacts",
        "llm_calls",
    ]:
        print(f"{key}={status.get(key, 0)}")
    for row in status.get("kg_sync_status", []):
        print(f"kg_sync {row['target']} {row['status']} counts={row['counts']} error={row['error']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Materials KG worker for Hypothesis Factory")
    parser.add_argument("--database-url", default=None, help="PostgreSQL URL or sqlite:/// path; defaults to CORPUS_DATABASE_URL or local corpus.db")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build materials KG layers and derived indexes")
    build.add_argument("--run-id", default="latest")
    build.add_argument("--stages", default=",".join(DEFAULT_STAGES), help="Comma-separated: sections,assets,entities,relations,embeddings,sync")
    build.add_argument("--grobid", choices=["auto", "always", "off"], default="auto")
    build.add_argument("--neo4j", choices=["auto", "off"], default="auto")
    build.add_argument("--qdrant", choices=["auto", "off", "required"], default="auto")
    build.set_defaults(func=build_command)

    build_all = sub.add_parser("build-all", help="Build KG/vector layers for every ingest run with chunks")
    build_all.add_argument("--stages", default=",".join(DEFAULT_STAGES), help="Comma-separated: sections,assets,entities,relations,embeddings,sync")
    build_all.add_argument("--grobid", choices=["auto", "always", "off"], default="auto")
    build_all.add_argument("--neo4j", choices=["auto", "off"], default="auto")
    build_all.add_argument("--qdrant", choices=["auto", "off", "required"], default="auto")
    build_all.add_argument("--limit-runs", type=int, default=0)
    build_all.add_argument("--fail-fast", action="store_true", help="Return non-zero when any run fails")
    build_all.set_defaults(func=build_all_command)

    status = sub.add_parser("status", help="Show KG/corpus status for a run")
    status.add_argument("--run-id", default="latest")
    status.set_defaults(func=status_command)

    search = sub.add_parser("search", help="Hybrid KG fallback search from Postgres ledger")
    search.add_argument("--run-id", default="latest")
    search.add_argument("--query", required=True)
    search.add_argument("--top-k", type=int, default=8)
    search.set_defaults(func=search_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
