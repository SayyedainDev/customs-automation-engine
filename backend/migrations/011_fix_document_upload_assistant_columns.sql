-- Up Migration
ALTER TABLE document_uploads ADD COLUMN indexing_status VARCHAR(50) NOT NULL DEFAULT 'pending';
ALTER TABLE document_uploads ADD COLUMN indexing_error TEXT;

-- Down Migration
ALTER TABLE document_uploads DROP COLUMN indexing_status;
ALTER TABLE document_uploads DROP COLUMN indexing_error;
