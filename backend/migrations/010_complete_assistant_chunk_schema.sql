-- Up Migration
ALTER TABLE shipment_document_chunks ADD COLUMN is_parent BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE shipment_document_chunks ADD COLUMN child_index INTEGER;

-- Down Migration
ALTER TABLE shipment_document_chunks DROP COLUMN IF EXISTS child_index;
ALTER TABLE shipment_document_chunks DROP COLUMN IF EXISTS is_parent;
