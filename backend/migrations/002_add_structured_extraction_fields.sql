ALTER TABLE document_uploads
    ADD COLUMN IF NOT EXISTS structured_data JSONB,
    ADD COLUMN IF NOT EXISTS structured_extraction_status VARCHAR(50)
        NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS structured_extraction_error TEXT,
    ADD COLUMN IF NOT EXISTS structured_extraction_model VARCHAR(255),
    ADD COLUMN IF NOT EXISTS structured_extracted_at TIMESTAMP WITH TIME ZONE;
