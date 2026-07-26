-- Store OCR derivatives separately from original embedded PDF text.
-- This column never replaces extracted_text or extracted_pages.
ALTER TABLE document_uploads
    ADD COLUMN IF NOT EXISTS ocr_pages JSONB;
