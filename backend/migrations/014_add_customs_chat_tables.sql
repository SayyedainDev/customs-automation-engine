-- Up Migration
--
-- Persistence for the general Pakistan Customs Assistant
-- (app/services/customs_chat), which supersedes the textile-only Regulatory
-- Chat. Kept as its own tables rather than reusing assistant_conversations/
-- assistant_messages: chat_turns records per-turn pipeline telemetry
-- (scope verdict, evidence band, guard violations, prompt version) that the
-- old assistant schema has no columns for, and that the evaluation harness
-- in docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 14 depends on.
--
-- Idempotent, matching the IF NOT EXISTS style of 007, 009 and 011.
CREATE TABLE IF NOT EXISTS chat_conversations (
    id UUID PRIMARY KEY,
    shipment_id UUID REFERENCES customs_audit_workflows(id),
    rolling_summary TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_active_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_chat_conversations_shipment_id ON chat_conversations(shipment_id);
CREATE INDEX IF NOT EXISTS ix_chat_conversations_last_active_at ON chat_conversations(last_active_at);

CREATE TABLE IF NOT EXISTS chat_turns (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES chat_conversations(id),
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    -- GROUNDED | GENERAL | OUT_OF_SCOPE | REFUSAL, null on the user's own turn.
    mode VARCHAR(32),
    -- customs_knowledge | document_help | tariff_lookup | compliance_check |
    -- meta | out_of_scope, null on the assistant's own turn.
    scope_intent VARCHAR(32),
    -- STRONG | WEAK | NONE | not_applicable.
    evidence_band VARCHAR(32),
    citations JSON,
    latency_ms DOUBLE PRECISION,
    tokens_in INTEGER,
    tokens_out INTEGER,
    model VARCHAR(128),
    prompt_version VARCHAR(32),
    guard_violations JSON,
    -- up | down | null, set later via the feedback endpoint.
    user_feedback VARCHAR(16),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_chat_turns_conversation_id ON chat_turns(conversation_id);
CREATE INDEX IF NOT EXISTS ix_chat_turns_created_at ON chat_turns(created_at);
CREATE INDEX IF NOT EXISTS ix_chat_turns_user_feedback ON chat_turns(user_feedback);


-- Down Migration
DROP TABLE IF EXISTS chat_turns;
DROP TABLE IF EXISTS chat_conversations;
