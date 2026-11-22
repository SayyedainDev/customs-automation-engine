FROM node:22-alpine AS frontend-build

WORKDIR /app/frontend

# Copy dependency metadata separately so Railway can reuse the npm layer when
# application source files change. package-lock.json is required by npm ci.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CUSTOMS_SERVE_FRONTEND=true

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
COPY backend/scripts/apply_migrations.py ./scripts/apply_migrations.py
COPY backend/scripts/bootstrap_runtime_data.py ./scripts/bootstrap_runtime_data.py
COPY backend/scripts/ingest_regulatory_documents.py \
    ./scripts/ingest_regulatory_documents.py
COPY backend/scripts/build_regulatory_vector_index.py \
    ./scripts/build_regulatory_vector_index.py
COPY backend/scripts/parse_tariff_pdf.py \
    ./scripts/parse_tariff_pdf.py
COPY backend/scripts/load_tariff_lines.py \
    ./scripts/load_tariff_lines.py
COPY synthetic_factory/clean_cotton_tshirts /app/synthetic_factory/clean_cotton_tshirts
COPY synthetic_factory/error_quantity_mismatch /app/synthetic_factory/error_quantity_mismatch
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist
# Runtime compliance rules plus the three curated sources needed if the
# regulatory ingestion service is run. The rest of the 161 MB source archive
# is reference material and is not needed by the API container.
COPY regulatory_data/config /app/regulatory_data/config
COPY regulatory_data/curated /app/regulatory_data/curated
COPY regulatory_data/processed /app/regulatory_data/processed
COPY regulatory_data/raw/commerce/export_policy/base_order \
    /app/regulatory_data/raw/commerce/export_policy/base_order
COPY regulatory_data/raw/psw/textile_product_requirements \
    /app/regulatory_data/raw/psw/textile_product_requirements
# Informational sources for the regulatory assistant (see sources.py).
COPY regulatory_data/raw/psw/single_declaration_export \
    /app/regulatory_data/raw/psw/single_declaration_export
COPY regulatory_data/raw/psw/user_manuals/tdap \
    /app/regulatory_data/raw/psw/user_manuals/tdap
COPY regulatory_data/raw/tdap/export_document_guides \
    /app/regulatory_data/raw/tdap/export_document_guides
COPY regulatory_data/raw/fbr/customs_act/customs_act_1969_updated_2025.pdf \
    /app/regulatory_data/raw/fbr/customs_act/customs_act_1969_updated_2025.pdf
COPY regulatory_data/raw/fbr/customs_rules/customs_rules_2001_sro_450_i_2001_updated_2025_08_31.pdf \
    /app/regulatory_data/raw/fbr/customs_rules/customs_rules_2001_sro_450_i_2001_updated_2025_08_31.pdf
COPY regulatory_data/raw/fbr/pct/pakistan_customs_tariff_fy_2025_26.pdf \
    /app/regulatory_data/raw/fbr/pct/pakistan_customs_tariff_fy_2025_26.pdf

RUN mkdir -p uploads/documents \
    && useradd --create-home --shell /usr/sbin/nologin customs \
    && chown -R customs:customs /app/backend

USER customs

EXPOSE 8000

CMD ["sh", "-c", "python -m app.core.init_db && python -m scripts.apply_migrations && python -m scripts.bootstrap_runtime_data && exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
