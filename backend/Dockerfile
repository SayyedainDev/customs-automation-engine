FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# This Dockerfile uses the repository root as its build context:
#   docker build -f backend/Dockerfile .
# Keeping backend/ below /app preserves the repository-relative paths used by
# the compliance and regulatory-data loaders.
WORKDIR /app/backend

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

RUN pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.10.0+cpu"

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/scripts/__init__.py ./scripts/__init__.py
COPY backend/scripts/ingest_regulatory_documents.py \
    ./scripts/ingest_regulatory_documents.py
COPY backend/scripts/build_regulatory_vector_index.py \
    ./scripts/build_regulatory_vector_index.py
# Runtime compliance rules plus the three curated sources needed if the
# regulatory ingestion service is run. The rest of the 161 MB source archive
# is reference material and is not needed by the API container.
COPY regulatory_data/config /app/regulatory_data/config
COPY regulatory_data/processed /app/regulatory_data/processed
COPY regulatory_data/raw/commerce/export_policy/base_order \
    /app/regulatory_data/raw/commerce/export_policy/base_order
COPY regulatory_data/raw/psw/textile_product_requirements \
    /app/regulatory_data/raw/psw/textile_product_requirements

RUN mkdir -p uploads/documents \
    && useradd --create-home --shell /usr/sbin/nologin customs \
    && chown -R customs:customs /app/backend

USER customs

EXPOSE 8000

CMD ["sh", "-c", "python -m app.core.init_db && exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
