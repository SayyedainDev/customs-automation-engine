-- Up Migration
--
-- Structured Pakistan Customs Tariff rows for exact/prefix HS-code lookup -
-- docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 7.5. Populated by
-- ``scripts/parse_tariff_pdf.py`` (writes a JSON artifact) then
-- ``scripts/load_tariff_lines.py`` (loads it here), not by the regulatory
-- RAG ingestion pipeline - a duty-rate answer must come from an exact row
-- lookup, never a retrieved passage.
CREATE TABLE IF NOT EXISTS tariff_lines (
    id UUID PRIMARY KEY,
    hs_code VARCHAR(16) NOT NULL UNIQUE,
    description TEXT NOT NULL,
    chapter VARCHAR(8),
    heading VARCHAR(8),
    customs_duty_pct DOUBLE PRECISION,
    additional_duty_pct DOUBLE PRECISION,
    regulatory_duty_pct DOUBLE PRECISION,
    sales_tax_pct DOUBLE PRECISION,
    income_tax_pct DOUBLE PRECISION,
    unit VARCHAR(32),
    tariff_year VARCHAR(16) NOT NULL,
    source_page INTEGER,
    needs_review BOOLEAN NOT NULL DEFAULT false,
    ingested_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_tariff_lines_hs_code ON tariff_lines(hs_code);
CREATE INDEX IF NOT EXISTS ix_tariff_lines_chapter ON tariff_lines(chapter);
CREATE INDEX IF NOT EXISTS ix_tariff_lines_heading ON tariff_lines(heading);


-- Down Migration
DROP TABLE IF EXISTS tariff_lines;
