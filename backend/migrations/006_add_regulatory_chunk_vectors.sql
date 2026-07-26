-- Phase 3B: persistent dense vector store for regulatory child chunks.
-- PostgreSQL. Idempotent create. chunk_id is the stable vector record ID.

CREATE TABLE IF NOT EXISTS regulatory_chunk_vectors (
    chunk_id          VARCHAR(255) PRIMARY KEY,
    parent_chunk_id   VARCHAR(255),
    collection        VARCHAR(128) NOT NULL,
    embedding         JSONB        NOT NULL,
    embedding_model   VARCHAR(255) NOT NULL,
    embedding_dim     INTEGER      NOT NULL,
    document_checksum VARCHAR(80)  NOT NULL,
    index_version     VARCHAR(64)  NOT NULL,
    meta              JSONB        NOT NULL,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_regulatory_chunk_vectors_parent
    ON regulatory_chunk_vectors (parent_chunk_id);
CREATE INDEX IF NOT EXISTS ix_regulatory_chunk_vectors_collection
    ON regulatory_chunk_vectors (collection);
CREATE INDEX IF NOT EXISTS ix_regulatory_chunk_vectors_checksum
    ON regulatory_chunk_vectors (document_checksum);
