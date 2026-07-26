# Customs-Audit Workflow Diagram (Phase 3C)

```mermaid
flowchart TD
    START([START]) --> LOAD[load_shipment_documents]
    LOAD --> BROKER[broker_agent<br/>runs existing extraction + matching + pipeline]
    BROKER --> DET[deterministic_compliance<br/>FREEZES authoritative status]
    DET --> AUD[auditor_agent<br/>independent re-derivation + RAG evidence]
    AUD --> CMP{compare_agent_reports<br/>structured consensus}
    CMP -->|consensus & no critical anomaly| FINAL[build_final_report]
    CMP -->|disagreement / uncertainty / critical anomaly| INT[interrupt_for_human_review<br/>persist state, pause]
    INT --> HDR[human_decision_received]
    HDR --> RES[resume_workflow<br/>apply corrections, re-run deterministic checks]
    RES --> FINAL
    FINAL --> PERSIST[persist_audit_record]
    PERSIST --> END([END])
```

## ASCII

```
START
  |
  v
load_shipment_documents
  |
  v
broker_agent  ---------------> (existing extraction/OCR/matching/compliance pipeline)
  |
  v
deterministic_compliance  ---> (freezes passed/failed/manual_review/not_applicable; never changed again)
  |
  v
auditor_agent  --------------> (independent checks + existing RAG evidence retrieval)
  |
  v
compare_agent_reports (deterministic structured consensus)
  |
  +-- consensus & no critical anomaly ----> build_final_report
  |                                              ^
  +-- disagreement / uncertainty / anomaly       |
          |                                       |
          v                                       |
   interrupt_for_human_review (persist, pause)    |
          |                                       |
          v                                       |
   human_decision_received                        |
          |                                       |
          v                                       |
   resume_workflow (apply corrections,            |
     re-run deterministic checks, new version) ---+
                                                  |
                                                  v
                                          persist_audit_record
                                                  |
                                                  v
                                                 END
```

Deterministic boundary: only the `deterministic_compliance` node's frozen result
(produced by the existing Python engine) decides the legal status. The Broker,
Auditor, consensus, RAG and any LLM narrator can route the workflow and explain
findings, but can never change that status.
```
