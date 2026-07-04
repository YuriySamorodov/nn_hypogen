#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.openalex import DEFAULT_SEARCH, DEFAULT_TOPICS, fetch_openalex_works


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export materials-science literature metadata from OpenAlex")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "data" / "materials_literature.jsonl"))
    parser.add_argument("--search", default=None, help="Optional full-text search; omit to use topic filters only")
    parser.add_argument("--topics", default=",".join(DEFAULT_TOPICS))
    parser.add_argument("--year-from", type=int, default=2015)
    parser.add_argument("--year-to", type=int, default=2024)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--mailto", default=None)
    args = parser.parse_args(argv)

    topic_ids = [item.strip() for item in args.topics.split(",") if item.strip()]
    payloads = fetch_openalex_works(
        search=args.search,
        topic_ids=topic_ids,
        year_from=args.year_from,
        year_to=args.year_to,
        limit=args.limit,
        mailto=args.mailto,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    print(f"exported={len(payloads)}")
    print(f"output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
