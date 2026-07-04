#!/usr/bin/env bash
set -euo pipefail

echo "Waiting for Qdrant at ${QDRANT_URL}..."
for _ in $(seq 1 60); do
  if curl -sf "${QDRANT_URL}/collections" >/dev/null 2>&1; then
    echo "Qdrant is ready."
    break
  fi
  sleep 2
done

if ! curl -sf "${QDRANT_URL}/collections" >/dev/null 2>&1; then
  echo "Qdrant is not reachable at ${QDRANT_URL}" >&2
  exit 1
fi

echo "Indexing corpus..."
python -m src.ingest --recreate

echo "Starting Chainlit on 0.0.0.0:8000..."
exec chainlit run app.py --host 0.0.0.0 --port 8000
