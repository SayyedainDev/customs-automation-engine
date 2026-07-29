-- Up Migration
--
-- Provenance kind and currency status for indexed regulatory chunks.
--
-- Before this, nothing in the schema distinguished a government notification
-- from a CACE-authored summary of one, so the console labelled every citation
-- "Official regulatory source" - including the curated PSW/TIPP summary. The
-- kind is now recorded at ingestion from the source registry rather than being
-- re-derived from the title at display time.
--
-- Nullable so the migration is safe on an already-populated corpus: rows
-- ingested before this column existed keep NULL and fall back to deterministic
-- classification, and a re-ingest fills them in.
ALTER TABLE regulatory_chunks
    ADD COLUMN IF NOT EXISTS source_kind VARCHAR(64);

-- "current" | "superseded" | "historical_reference"
ALTER TABLE regulatory_chunks
    ADD COLUMN IF NOT EXISTS currency_status VARCHAR(32);

CREATE INDEX IF NOT EXISTS ix_regulatory_chunks_source_kind
    ON regulatory_chunks(source_kind);

-- Down Migration
DROP INDEX IF EXISTS ix_regulatory_chunks_source_kind;
ALTER TABLE regulatory_chunks DROP COLUMN IF EXISTS currency_status;
ALTER TABLE regulatory_chunks DROP COLUMN IF EXISTS source_kind;
