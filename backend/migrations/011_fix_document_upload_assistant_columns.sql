-- Up Migration
ALTER TABLE document_uploads ADD COLUMN IF NOT EXISTS indexing_status VARCHAR(50) NOT NULL DEFAULT 'pending';
ALTER TABLE document_uploads ADD COLUMN IF NOT EXISTS indexing_error TEXT;

-- Down Migration
ALTER TABLE document_uploads DROP COLUMN IF EXISTS indexing_status;
ALTER TABLE document_uploads DROP COLUMN IF EXISTS indexing_error;
