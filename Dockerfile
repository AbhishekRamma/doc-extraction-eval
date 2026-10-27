# App image: Python + Tesseract OCR (the small baseline engine).
# docTR/torch is NOT installed here to keep the image small, so the
# "auto" OCR engine resolves to tesseract inside the container.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Install dependencies first so code edits don't bust the pip cache layer
COPY pyproject.toml ./
COPY src/__init__.py src/__init__.py
COPY src/prompts src/prompts
RUN pip install --no-cache-dir -e .

COPY . .
RUN chmod +x scripts/docker_entrypoint.sh

ENTRYPOINT ["scripts/docker_entrypoint.sh"]
