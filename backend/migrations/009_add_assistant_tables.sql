-- Up Migration
CREATE TABLE assistant_conversations (
    id UUID PRIMARY KEY,
    shipment_id UUID REFERENCES customs_audit_workflows(id),
    mode VARCHAR(64) NOT NULL,
    pct_code VARCHAR(32),
    destination VARCHAR(128),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX ix_assistant_conversations_shipment_id ON assistant_conversations(shipment_id);
CREATE INDEX ix_assistant_conversations_created_at ON assistant_conversations(created_at);

CREATE TABLE assistant_messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES assistant_conversations(id),
    role VARCHAR(16) NOT NULL,
    text TEXT NOT NULL,
    answer_type VARCHAR(64),
    sources JSON,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX ix_assistant_messages_conversation_id ON assistant_messages(conversation_id);
CREATE INDEX ix_assistant_messages_created_at ON assistant_messages(created_at);

CREATE TABLE shipment_document_chunks (
    id UUID PRIMARY KEY,
    shipment_id UUID NOT NULL REFERENCES customs_audit_workflows(id),
    workflow_id UUID,
    document_id UUID NOT NULL REFERENCES document_uploads(id),
    document_version INTEGER NOT NULL DEFAULT 1,
    document_type VARCHAR(128) NOT NULL,
    document_name VARCHAR(255) NOT NULL,
    page_number INTEGER NOT NULL,
    section VARCHAR(255) NOT NULL,
    pct_code VARCHAR(32),
    invoice_number VARCHAR(128),
    source_kind VARCHAR(64) NOT NULL DEFAULT 'uploaded_document',
    parent_chunk_id UUID,
    child_chunk_id UUID,
    content_hash VARCHAR(128) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    text TEXT NOT NULL,
    search_text TEXT NOT NULL,
    embedding JSON,
    embedding_model VARCHAR(255),
    embedding_dim INTEGER,
    meta_data JSON,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    deactivated_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX ix_shipment_document_chunks_shipment_id ON shipment_document_chunks(shipment_id);
CREATE INDEX ix_shipment_document_chunks_document_id ON shipment_document_chunks(document_id);
CREATE INDEX ix_shipment_document_chunks_document_type ON shipment_document_chunks(document_type);
CREATE INDEX ix_shipment_document_chunks_parent_chunk_id ON shipment_document_chunks(parent_chunk_id);
CREATE INDEX ix_shipment_document_chunks_content_hash ON shipment_document_chunks(content_hash);
CREATE INDEX ix_shipment_document_chunks_active ON shipment_document_chunks(active);
CREATE INDEX ix_shipment_document_chunks_created_at ON shipment_document_chunks(created_at);


-- Down Migration
DROP TABLE IF EXISTS shipment_document_chunks;
DROP TABLE IF EXISTS assistant_messages;
DROP TABLE IF EXISTS assistant_conversations;
