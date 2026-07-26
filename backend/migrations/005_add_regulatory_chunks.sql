-- Stage 3: regulatory evidence chunk store (parent/child chunks with provenance).
-- PostgreSQL. Idempotent create.

CREATE TABLE IF NOT EXISTS regulatory_chunks (
    id                UUID PRIMARY KEY,
    chunk_id          VARCHAR(255) NOT NULL UNIQUE,
    parent_chunk_id   VARCHAR(255),
    role              VARCHAR(16)  NOT NULL,
    is_parent         BOOLEAN      NOT NULL DEFAULT FALSE,
    chunk_index       INTEGER      NOT NULL,
    source_document   VARCHAR(512) NOT NULL,
    source_path       VARCHAR(1024) NOT NULL,
    source_url        VARCHAR(1024),
    document_checksum VARCHAR(80)  NOT NULL,
    issuing_authority VARCHAR(512),
    document_type     VARCHAR(128) NOT NULL,
    sro_number        VARCHAR(64),
    page_number       INTEGER,
    section           VARCHAR(512),
    pct_codes         JSONB,
    issue_date        DATE,
    effective_date    DATE,
    legal_cutoff_date DATE,
    validation_status VARCHAR(64)  NOT NULL,
    rule_data_version VARCHAR(120) NOT NULL,
    ingestion_version VARCHAR(64)  NOT NULL,
    ingested_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    text              TEXT         NOT NULL,
    char_count        INTEGER      NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_regulatory_chunks_parent_chunk_id
    ON regulatory_chunks (parent_chunk_id);
CREATE INDEX IF NOT EXISTS ix_regulatory_chunks_source_path
    ON regulatory_chunks (source_path);
CREATE INDEX IF NOT EXISTS ix_regulatory_chunks_document_checksum
    ON regulatory_chunks (document_checksum);
