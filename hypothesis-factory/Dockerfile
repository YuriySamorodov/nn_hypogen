FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      curl \
      nodejs \
      npm \
      poppler-utils \
      tesseract-ocr \
      tesseract-ocr-rus \
      tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Repomix is optional at runtime. If npm registry is unavailable, the worker
# records repomix_pack as skipped instead of breaking ingestion.
RUN npm install -g repomix || true

COPY . /app

CMD ["python", "-m", "backend.corpus_worker", "status", "--run-id", "latest"]
