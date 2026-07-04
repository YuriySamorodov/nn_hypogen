FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

ENV QDRANT_URL=http://qdrant:6333
ENV ENABLE_OCR=true
ENV CHAINLIT_NO_TELEMETRY=true

EXPOSE 8000

CMD ["bash", "scripts/start.sh"]
