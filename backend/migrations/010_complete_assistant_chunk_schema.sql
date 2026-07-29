-- Up Migration
--
-- Idempotent, matching 007/009/011: a database bootstrapped from the ORM
-- metadata already carries these columns.
ALTER TABLE shipment_document_chunks
    ADD COLUMN IF NOT EXISTS is_parent BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE shipment_document_chunks
    ADD COLUMN IF NOT EXISTS child_index INTEGER;

-- Down Migration
ALTER TABLE shipment_document_chunks DROP COLUMN IF EXISTS child_index;
ALTER TABLE shipment_document_chunks DROP COLUMN IF EXISTS is_parent;
