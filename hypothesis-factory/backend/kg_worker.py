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
    result: dict[str, Any] = {"run_id": run_id, "stages": stages}
    try:
        if "sections" in stages or "assets" in stages:
            result["document_layers"] = build_document_layers(store, run_id, grobid=args.grobid)
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
            if args.neo4j != "off":
                result["neo4j"] = sync_neo4j(store, run_id)
            else:
                store.upsert_kg_sync_status(run_id, "neo4j", "skipped", error="disabled by --neo4j off")
                result["neo4j"] = {"status": "skipped", "error": "disabled by --neo4j off"}
            if args.qdrant != "off":
                result["qdrant"] = sync_qdrant(store, run_id)
            else:
                store.upsert_kg_sync_status(run_id, "qdrant", "skipped", error="disabled by --qdrant off")
                result["qdrant"] = {"status": "skipped", "error": "disabled by --qdrant off"}
        status = store.run_status(run_id)
    finally:
        store.close()
    print_build_result(result)
    print_status(status)
    return 0


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
    build.add_argument("--qdrant", choices=["auto", "off"], default="auto")
    build.set_defaults(func=build_command)

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
