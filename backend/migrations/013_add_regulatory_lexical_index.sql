-- Up Migration
--
-- Persistent lexical search for the regulatory corpus.
--
-- Retrieval built its BM25 index in Python on every request, over every child
-- chunk in the corpus. At 374 chunks that was affordable; the Customs Act,
-- the Customs Rules and the textile tariff chapters take the corpus into the
-- thousands, where an O(corpus) rebuild per question is not.
--
-- The column is GENERATED ... STORED so the vector cannot drift from the text:
-- there is no trigger to forget and no application code that can skip the
-- update. to_tsvector with a literal regconfig is immutable, which is what
-- makes a generated column legal here.
--
-- Deliberately not added to the SQLAlchemy model: the test suite runs on
-- SQLite, which has no tsvector, and the search path falls back to the
-- in-memory index when the dialect cannot offer this one.
ALTER TABLE regulatory_chunks
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;

CREATE INDEX IF NOT EXISTS ix_regulatory_chunks_search_vector
    ON regulatory_chunks USING GIN (search_vector);

-- Filters applied before ranking, so the candidate set is narrowed in the
-- database rather than by scanning the whole corpus in Python.
CREATE INDEX IF NOT EXISTS ix_regulatory_chunks_child_lookup
    ON regulatory_chunks (is_parent, validation_status);

-- Down Migration
DROP INDEX IF EXISTS ix_regulatory_chunks_child_lookup;
DROP INDEX IF EXISTS ix_regulatory_chunks_search_vector;
ALTER TABLE regulatory_chunks DROP COLUMN IF EXISTS search_vector;
