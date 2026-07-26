-- Apply this once to databases created before extraction result persistence
-- was introduced. Fresh databases receive these columns from SQLAlchemy's
-- metadata via `python -m app.core.init_db`.
ALTER TABLE document_uploads
    ADD COLUMN IF NOT EXISTS extracted_text TEXT,
    ADD COLUMN IF NOT EXISTS page_count INTEGER,
    ADD COLUMN IF NOT EXISTS character_count INTEGER,
    ADD COLUMN IF NOT EXISTS extraction_error TEXT,
    ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMP WITH TIME ZONE;
