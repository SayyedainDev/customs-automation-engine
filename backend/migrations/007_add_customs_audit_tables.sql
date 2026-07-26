-- Phase 3C: LangGraph multi-agent customs-audit workflow tables. PostgreSQL.
-- The resumable graph state lives in the LangGraph checkpointer; these tables
-- are the queryable projection plus the immutable audit trail.

CREATE TABLE IF NOT EXISTS customs_audit_workflows (
    id                       UUID PRIMARY KEY,
    thread_id                VARCHAR(128) NOT NULL UNIQUE,
    status                   VARCHAR(48)  NOT NULL,
    current_node             VARCHAR(64),
    invoice_document_id      UUID,
    packing_list_document_id UUID,
    deterministic_status     VARCHAR(48),
    requires_human_review    BOOLEAN      NOT NULL DEFAULT FALSE,
    rule_data_version        VARCHAR(120),
    vector_index_version     VARCHAR(64),
    final_report             JSONB,
    errors                   JSONB,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at             TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_caw_status      ON customs_audit_workflows (status);
CREATE INDEX IF NOT EXISTS ix_caw_thread      ON customs_audit_workflows (thread_id);
CREATE INDEX IF NOT EXISTS ix_caw_invoice     ON customs_audit_workflows (invoice_document_id);
CREATE INDEX IF NOT EXISTS ix_caw_packing     ON customs_audit_workflows (packing_list_document_id);
CREATE INDEX IF NOT EXISTS ix_caw_created     ON customs_audit_workflows (created_at);

CREATE TABLE IF NOT EXISTS customs_human_review_tasks (
    id                   UUID PRIMARY KEY,
    workflow_id          UUID NOT NULL REFERENCES customs_audit_workflows (id),
    status               VARCHAR(32) NOT NULL,
    reason               TEXT        NOT NULL,
    disputed_fields      JSONB,
    requested_actions    JSONB,
    evidence             JSONB,
    deterministic_status VARCHAR(48),
    assigned_to          VARCHAR(128),
    decision             JSONB,
    review_note          TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_chrt_workflow ON customs_human_review_tasks (workflow_id);
CREATE INDEX IF NOT EXISTS ix_chrt_status   ON customs_human_review_tasks (status);

CREATE TABLE IF NOT EXISTS customs_audit_events (
    id                  UUID PRIMARY KEY,
    workflow_id         UUID NOT NULL REFERENCES customs_audit_workflows (id),
    event_type          VARCHAR(64) NOT NULL,
    node_name           VARCHAR(64),
    actor_type          VARCHAR(32) NOT NULL,
    actor_reference     VARCHAR(128),
    previous_state_hash VARCHAR(80),
    new_state_hash      VARCHAR(80),
    event_payload       JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cae_workflow ON customs_audit_events (workflow_id);
CREATE INDEX IF NOT EXISTS ix_cae_type     ON customs_audit_events (event_type);
CREATE INDEX IF NOT EXISTS ix_cae_created  ON customs_audit_events (created_at);
