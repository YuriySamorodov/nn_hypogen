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
      procps \
      tesseract-ocr \
      tesseract-ocr-rus \
      tesseract-ocr-eng \
      libgl1 \
      libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# GPU OCR stack (EasyOCR on CUDA). The default PyPI torch/torchvision wheels
# bundle CUDA/cuDNN, so no CUDA base image is needed; the NVIDIA container
# runtime injects the GPU (see NVIDIA_VISIBLE_DEVICES in docker-compose.yml).
RUN pip install --no-cache-dir easyocr==1.7.2

# Repomix is optional at runtime. If npm registry is unavailable, the worker
# records repomix_pack as skipped instead of breaking ingestion.
RUN npm install -g repomix || true

COPY . /app

CMD ["python", "-m", "backend.corpus_worker", "status", "--run-id", "latest"]
