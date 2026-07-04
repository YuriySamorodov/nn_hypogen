from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.kg_worker import DEFAULT_STAGES, build_run, runs_with_chunks
from backend.services.corpus_db import CorpusStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Build KG embeddings and derived vector indexes for existing corpus runs")
    parser.add_argument("--database-url", default=None)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all-runs", action="store_true")
    target.add_argument("--run-id")
    parser.add_argument("--stages", default=",".join(DEFAULT_STAGES))
    parser.add_argument("--grobid", choices=["auto", "always", "off"], default="auto")
    parser.add_argument("--neo4j", choices=["auto", "off"], default="auto")
    parser.add_argument("--qdrant", choices=["auto", "off", "required"], default="auto")
    parser.add_argument("--limit-runs", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stages = [stage.strip() for stage in args.stages.split(",") if stage.strip()]
    store = CorpusStore(args.database_url)
    store.initialize_schema()
    failures = 0
    try:
        rows = _target_runs(store, args)
        print("Vector index build plan")
        print(f"stages={','.join(stages)}")
        print(f"qdrant={args.qdrant}")
        print(f"runs={len(rows)}")
        for row in rows:
            print(
                "status "
                f"run_id={row['run_id']} name={row['name']} run_status={row['status']} "
                f"documents={row['documents']} chunks={row['chunks']} existing_embeddings={row['kg_embeddings']}"
            )
        if args.dry_run:
            return 0

        for row in rows:
            run_id = str(row["run_id"])
            try:
                result = build_run(store, run_id, stages, grobid=args.grobid, neo4j=args.neo4j, qdrant=args.qdrant)
                print(
                    "built "
                    f"run_id={run_id} embeddings={result.get('embeddings', 'n/a')} "
                    f"qdrant={result.get('qdrant', {}).get('status', 'not_requested')}"
                )
                if args.qdrant == "required" and result.get("qdrant", {}).get("status") != "completed":
                    failures += 1
            except Exception as exc:
                failures += 1
                store.upsert_kg_sync_status(run_id, "kg_worker", "failed", error=str(exc))
                print(f"failed run_id={run_id} error={exc}", file=sys.stderr)
    finally:
        store.close()
    print(f"completed={len(rows) - failures}")
    print(f"failed={failures}")
    return 2 if failures else 0


def _target_runs(store: CorpusStore, args: argparse.Namespace) -> list[dict]:
    if args.all_runs:
        rows = runs_with_chunks(store)
        return rows[: args.limit_runs] if args.limit_runs else rows
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    rows = [row for row in runs_with_chunks(store) if row["run_id"] == run_id]
    if rows:
        return rows
    status = store.run_status(run_id)
    return [
        {
            "run_id": run_id,
            "name": status["run"]["name"],
            "status": status["run"]["status"],
            "documents": status["document_texts"],
            "chunks": status["document_chunks"],
            "kg_embeddings": status["kg_embeddings"],
        }
    ]


if __name__ == "__main__":
    raise SystemExit(main())
