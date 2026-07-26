-- Phase X: persistent dense vector store for historical-shipment semantic
-- search. PostgreSQL. Idempotent create. One row per finalized
-- customs_audit_workflows entry - not the same table as
-- regulatory_chunk_vectors, which indexes legal text, not shipment summaries.

CREATE TABLE IF NOT EXISTS shipment_summary_vectors (
    workflow_id       UUID         PRIMARY KEY REFERENCES customs_audit_workflows(id),
    summary_text      TEXT         NOT NULL,
    embedding         JSONB        NOT NULL,
    embedding_model   VARCHAR(255) NOT NULL,
    embedding_dim     INTEGER      NOT NULL,
    summary_checksum  VARCHAR(80)  NOT NULL,
    index_version     VARCHAR(64)  NOT NULL,
    meta              JSONB        NOT NULL,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_shipment_summary_vectors_checksum
    ON shipment_summary_vectors (summary_checksum);
