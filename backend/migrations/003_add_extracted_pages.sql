-- Preserve page boundaries and page-level text for provenance-aware extraction.
-- Apply once to databases created before Phase 2A.
ALTER TABLE document_uploads
    ADD COLUMN IF NOT EXISTS extracted_pages JSONB;
