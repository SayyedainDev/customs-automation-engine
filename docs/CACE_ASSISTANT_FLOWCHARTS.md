# CACE Assistant Flowcharts

> Generated from read-only code inspection — not from specification documents.
> Every node corresponds to real executable code in the repository.

---

## Flowchart 1 — Complete Two-Mode Assistant

```mermaid
graph TD
    User["User opens CACE"]
    User -->|"/prepare"| Mode1["Mode 1: Prepare an Export"]
    User -->|"/review → agent audit"| Mode2["Mode 2: Ask About Shipment"]

    Mode1 --> FE1["PrepareExportPage.tsx"]
    FE1 -->|"POST /api/v1/assistant/guidance"| BE1["assistant.py → generate_pre_submission_guidance()"]
    BE1 --> R1["GuidanceResponse with document cards"]
    R1 --> FE1

    Mode2 --> FE2["AssistantPanel.tsx"]
    FE2 -->|"POST /api/v1/assistant/shipments/{id}/chat"| BE2["assistant.py → answer_shipment_question()"]
    BE2 --> R2["ChatResponse with sources"]
    R2 --> FE2

    style FE1 fill:#e6f3ff
    style FE2 fill:#e6f3ff
    style BE1 fill:#f0fff0
    style BE2 fill:#f0fff0
```

---

## Flowchart 2 — Pre-Submission Guidance

```mermaid
graph TD
    Input["Product + PCT + Destination"]
    Input --> Norm["normalize_pct_code()"]
    Norm --> LenCheck{"len == 8?"}
    LenCheck -->|No| Reject6["Reject: requires 8-digit code"]
    LenCheck -->|Yes| ScopeCheck{"In SUPPORTED_PCT_PRODUCTS?"}
    ScopeCheck -->|No| RejectUnsup["Reject: outside 5-PCT scope"]
    ScopeCheck -->|Yes| ConflictCheck{"Product/PCT conflict?"}
    ConflictCheck -->|Yes| RejectConflict["Reject: inconsistent product"]
    ConflictCheck -->|No| DestCheck{"destination empty?"}
    DestCheck -->|Yes| AskDest["Return: provide destination"]
    DestCheck -->|No| RuleEngine["get_compliance_rule_engine().check()"]
    RuleEngine --> Outstanding["collect_outstanding_documents()"]
    Outstanding --> RegRAG["run_evidence_search() per document"]
    RegRAG --> EvidenceGate{"evidence found?"}
    EvidenceGate -->|Yes| Available["evidence_status = available"]
    EvidenceGate -->|No| Unavailable["evidence_status = unavailable"]
    Available --> BuildDocs["Build DocumentGuidanceSchema list"]
    Unavailable --> BuildDocs
    BuildDocs --> AddBaseline["Prepend Commercial Invoice + Packing List"]
    AddBaseline --> GuidanceResp["Return GuidanceResponse"]

    style Reject6 fill:#ffcccc
    style RejectUnsup fill:#ffcccc
    style RejectConflict fill:#ffcccc
    style RuleEngine fill:#ccffcc
    style RegRAG fill:#ffffcc
```

> **DEFECT**: GuidanceRequest schema requires `question: str` (no default).
> Frontend does not send it. This causes a **422 Unprocessable Entity** on every request.

---

## Flowchart 3 — Document Indexing

```mermaid
graph TD
    Upload["Document Upload"] --> Extract["PDF extraction + OCR"]
    Extract --> StructExtract["Structured extraction (Groq)"]
    StructExtract --> DBPersist["Persist to document_uploads"]
    DBPersist --> MultiLine["multi_line_shipment_service.run_full_analysis()"]
    MultiLine --> IndexCall["index_shipment_documents()"]

    IndexCall --> ForEachDoc["For each (doc_id, doc_type)"]
    ForEachDoc --> HasExtracted{"doc.extracted_pages?"}
    HasExtracted -->|No| SkipDoc["indexing_status = skipped_unchanged"]
    HasExtracted -->|Yes| AlreadyIndexed{"Active chunks exist?"}
    AlreadyIndexed -->|Yes| SkipUnchanged["indexing_status = skipped_unchanged"]
    AlreadyIndexed -->|No| Deactivate["Deactivate old same-type chunks"]
    Deactivate --> BuildChunks["build_semantic_chunks_for_document()"]
    BuildChunks --> SplitPara["Split pages → paragraphs"]
    SplitPara --> ClassifySection["Heuristic section classification"]
    ClassifySection --> CreateParent["Create parent chunk"]
    CreateParent --> SplitChild["Split paragraph → child chunks"]
    SplitChild --> GenHash["SHA-256 content hash"]
    GenHash --> GenEmbed["get_embedding_provider().embed()"]
    GenEmbed -->|Success| AddToDB["db.add_all(chunks)"]
    GenEmbed -->|Failure| IndexFail["indexing_status = failed"]
    AddToDB --> IndexSuccess["indexing_status = indexed"]
    IndexSuccess --> Commit["db.commit()"]

    style IndexCall fill:#ccffcc
    style GenEmbed fill:#ffffcc
    style IndexFail fill:#ffcccc
```

> **Note**: Indexing is triggered from `multi_line_shipment_service` (deterministic compliance check),
> **not** from broker_agent or LangGraph. This means indexing happens before agent audit.

---

## Flowchart 4 — Shipment Question Routing

```mermaid
graph TD
    Q["User question"]
    Q --> LoadConv["Load conversation (if ID provided)"]
    LoadConv --> PronounResolve["Pronoun reference resolution"]
    PronounResolve --> FindWorkflow["Lookup CustomsAuditWorkflow"]
    FindWorkflow --> Route["classify_question()"]

    Route --> KW{"Keyword match?"}
    KW -->|Yes| Determined["Return matched route"]
    KW -->|No| Heuristic["Heuristic fallback"]
    Heuristic --> Default["Default: shipment_document_fact"]

    Determined --> Branch{Route}
    Default --> Branch

    Branch -->|out_of_scope| OOS["Reject: cannot change values"]
    Branch -->|audit_result| AuditBranch["Frozen audit status + events"]
    Branch -->|shipment_document_fact| FactBranch["Structured data or RAG"]
    Branch -->|regulatory_guidance| RegPlaceholder["Regulatory RAG (run_evidence_search)"]
    Branch -->|combined_shipment_and_regulation| CombPlaceholder["Combine structured + audit + regulatory"]
    Branch -->|audit_history| NoHandler["Retrieve audit history revisions"]
    Branch -->|pre_submission_guidance| NoHandler2["NO HANDLER: falls to default"]

    FactBranch --> FactKW{"Keyword in question?"}
    FactKW -->|"invoice total"| Structured["Return structured_data field"]
    FactKW -->|"buyer"| Structured
    FactKW -->|"quantity"| Structured
    FactKW -->|"match"| AuditStatus["Return workflow.status"]
    FactKW -->|Other| ShipRAG["ShipmentDocumentRetriever.retrieve()"]

    AuditBranch --> SaveMsg["Persist user + assistant messages"]
    FactBranch --> SaveMsg
    OOS --> SaveMsg
    RegPlaceholder --> SaveMsg
    CombPlaceholder --> SaveMsg
    NoHandler --> SaveMsg
    SaveMsg --> ChatResp["Return ChatResponse"]

    style RegPlaceholder fill:#ccffcc
    style CombPlaceholder fill:#ccffcc
    style NoHandler fill:#ccffcc
    style NoHandler2 fill:#ffcccc
    style Structured fill:#ccffcc
    style ShipRAG fill:#ffffcc
```

---

## Flowchart 5 — Shipment Hybrid RAG

```mermaid
graph TD
    Query["Search query"]
    Query --> DBFilter["SELECT WHERE shipment_id = ? AND active = True"]
    DBFilter --> SplitPC["Separate parents and children"]
    SplitPC --> BM25["BM25 on child chunks"]
    SplitPC --> Dense["Dense embedding similarity"]

    BM25 --> BM25Rank["BM25 rank map"]
    Dense --> DenseRank["Dense rank map"]

    BM25Rank --> RRF["Reciprocal Rank Fusion (k=60)"]
    DenseRank --> RRF
    RRF --> TopCandidates["Top 2*k candidates"]
    TopCandidates --> Reranker["Cross-encoder reranker.score()"]
    Reranker -->|Success| RerankedOrder["Reranked order"]
    Reranker -->|Exception| DegradedMode["Degraded: use RRF scores"]

    RerankedOrder --> EvidenceGate["_evaluate_evidence_gate()"]
    DegradedMode --> EvidenceGate

    EvidenceGate --> Threshold{"reranker_score >= -2.0?"}
    Threshold -->|No| Unavailable["shipment_evidence_unavailable → skip"]
    Threshold -->|Yes| DocTypeCheck{"Query doc type matches chunk?"}
    DocTypeCheck -->|No| Conflicting["shipment_evidence_conflicting → skip"]
    DocTypeCheck -->|Yes| SectionCheck{"Section relevant?"}
    SectionCheck -->|Partial| Partial["shipment_evidence_partial → include"]
    SectionCheck -->|Yes| Verified["shipment_evidence_verified → include"]

    Verified --> ParentLookup["Resolve parent chunk text"]
    Partial --> ParentLookup
    ParentLookup --> RetrievedChunk["Return RetrievedChunk list"]

    style DBFilter fill:#ccffcc
    style BM25 fill:#ccffcc
    style Dense fill:#ffffcc
    style Reranker fill:#ffffcc
    style EvidenceGate fill:#ccffcc
```

---

## Flowchart 6 — Combined Answer
```mermaid
graph TD
    CombinedQ["User asks combined question"]
    CombinedQ --> Route["classify_question() → combined_shipment_and_regulation"]
    Route --> Extracted["Add structured extraction source"]
    Extracted --> Audit["Add frozen audit status source"]
    Audit --> RegSearch["run_evidence_search(query, pct, dest)"]
    RegSearch --> Found{"Evidence found?"}
    Found -->|Yes| AddReg["Add regulatory source"]
    Found -->|No| SkipReg["Skip regulatory source"]
    AddReg --> AddRule["Add configured requirement source"]
    SkipReg --> AddRule
    AddRule --> Answer["Explain deterministic match & missing external authentication"]

    style RegSearch fill:#ffffcc
    style Extracted fill:#ccffcc
    style Audit fill:#ccffcc
    style AddReg fill:#ccffcc
    style AddRule fill:#ccffcc
```

> **Note**: Combined answers explicitly combine the uploaded document extraction, the deterministic audit result, and the regulatory citation into one grounded response, while stating CACE's authenticity limitations.

---

## Flowchart 7 — Conversation Follow-Up

```mermaid
graph TD
    NewMsg["User sends follow-up"]
    NewMsg --> LoadConv["Load AssistantConversation by ID"]
    LoadConv --> LoadLast["Load last assistant message"]
    LoadLast --> ExtractDoc["Extract last source document name"]
    ExtractDoc --> PronounCheck{"Question contains 'it', 'that', 'this'?"}
    PronounCheck -->|Yes| Augment["Append document name to search query"]
    PronounCheck -->|No| PassThrough["Use question as-is"]
    Augment --> Route["classify_question()"]
    PassThrough --> Route
    Route --> Answer["Generate answer"]
    Answer --> Persist["Save user + assistant messages"]
    Persist --> Response["Return ChatResponse with same conversation_id"]

    style Augment fill:#ffffcc
```

> **Honest description**: This is **deterministic limited follow-up resolution**,
> not general human-level conversation understanding.
> It only resolves pronouns by appending the last-cited document name.
> Previous assistant prose is never used as evidence source.

---

## Flowchart 8 — Safety Boundary

```mermaid
graph TD
    DocText["Uploaded document text"]
    DocText --> Indexed["Indexed as ShipmentDocumentChunk"]
    Indexed --> UntrustedEvidence["Treated as untrusted evidence"]
    UntrustedEvidence --> CannotInstruct["Cannot issue application instructions"]
    UntrustedEvidence --> CannotChangeStatus["Cannot change audit status"]

    Chat["Chat input"]
    Chat --> RouteCheck["classify_question()"]
    RouteCheck -->|"change/mark"| OOS["out_of_scope: rejected"]
    RouteCheck -->|Normal| Normal["Normal answer path"]

    Safety["Safety boundaries"]
    Safety --> S1["Chat cannot change extraction"]
    Safety --> S2["Chat cannot change audit status"]
    Safety --> S3["Chat cannot invoke human correction"]
    Safety --> S4["Chat cannot claim external authenticity"]
    Safety --> S5["Chat cannot claim customs clearance"]

    AuditQ["Audit question"]
    AuditQ --> FrozenOnly["Uses only workflow.status from DB"]
    FrozenOnly --> NeverRecalc["Never recalculates compliance"]

    style OOS fill:#ccffcc
    style FrozenOnly fill:#ccffcc
```

> **Prompt injection protection**: The system does not pass document text as
> system instructions. For audit questions, it reads only the frozen
> `workflow.status` column, making injection irrelevant for compliance answers.
> For RAG-based answers, document text appears as cited content, not instructions.

---

## Flowchart 9 — Current Deployment Boundary

```mermaid
graph TD
    Current["Current: Single-user prototype"]
    Current --> NoAuth["No authentication"]
    Current --> NoTenant["No tenant isolation"]
    Current --> ShipFilter["Shipment-level DB filtering only"]
    Current --> NotSafe["NOT safe for multi-user public deployment"]

    Future["Future production requirements"]
    Future --> F1["Authentication (JWT/OAuth)"]
    Future --> F2["owner_id / tenant_id on all models"]
    Future --> F3["Stable shipment_context_id"]
    Future --> F4["Cross-tenant isolation tests"]
    Future --> F5["Rate limiting"]
    Future --> F6["Audit logging per user"]

    style NotSafe fill:#ffcccc
    style Current fill:#ffffcc
```

---

## Flowchart 10 — Failure and Fallback Paths

```mermaid
graph TD
    F1["Embedding unavailable"] --> F1F["build_semantic_chunks raises exception"]
    F1F --> F1R["indexing_status = failed; extraction NOT affected"]

    F2["Reranker unavailable"] --> F2F["Exception caught in retrieve()"]
    F2F --> F2R["Degraded mode: RRF scores used directly"]

    F3["Indexing failure"] --> F3F["doc.indexing_status = 'failed'"]
    F3F --> F3R["doc.indexing_error = str(exc)"]

    F4["Evidence unavailable"] --> F4F["run_evidence_search returns empty"]
    F4F --> F4R["evidence_status = unavailable; message appended"]

    F5["Audit not started"] --> F5F["workflow is None"]
    F5F --> F5R["'audit workflow has not been started yet'"]

    F6["Unsupported PCT"] --> F6F["validate_pct_scope returns False"]
    F6F --> F6R["GuidanceResponse.supported_scope = False"]

    F7["Backend unavailable"] --> F7F["Frontend catch block"]
    F7F --> F7R["Error state displayed in UI"]

    F8["Ambiguous follow-up"] --> F8F["No pronoun detected"]
    F8F --> F8R["Question used as-is (may return wrong result)"]

    style F1R fill:#ffffcc
    style F2R fill:#ffffcc
    style F5R fill:#ccffcc
    style F6R fill:#ccffcc
```

---

## Limitations Map

| # | Limitation | Affected Component | Current Behavior | User-Visible Impact | Security Impact | Presentation Wording | Future Fix | Priority |
|---|---|---|---|---|---|---|---|---|
| 1 | Single-user mode | Entire system | No user accounts | None for demo | None for single user | "Single-user prototype" | Add auth | Future production |
| 2 | No authentication | All routes | Open access | None for demo | **Critical before multi-user** | "No authentication" | JWT/OAuth | Security-critical |
| 3 | No tenant isolation | All models | No owner_id | None for demo | **Critical before multi-user** | Omit from demo | Add tenant_id | Security-critical |
| 4 | Shipment key = invoice doc ID | ShipmentDocumentChunk, conversations | Invoice replacement orphans context | Conversations lost on re-upload | Low | "Prototype identifier" | Add shipment_context_id | Future production |
| 5 | Invoice replacement orphans context | Conversations, chunks | No re-linking mechanism | Old conversations inaccessible | Low | Omit | Implement re-linking | Future production |
| 6 | Five PCT codes only | foundation.py | Hard reject for others | Cannot use for non-textile | None | "Five-PCT prototype scope" | Expand rule config | Accepted capstone |
| 7 | No broad tariff coverage | Rule engine | Only textile rules | Limited product range | None | "Textile focus" | Expand | Accepted capstone |
| 8 | Limited destination coverage | Regulatory RAG | Only indexed destinations | Some destinations lack evidence | None | "Evidence may be unavailable" | Index more | Accepted capstone |
| 9 | Regulatory corpus may be outdated | Evidence search | Static snapshot | May cite old laws | None | "Curated snapshot dated..." | Auto-refresh | Future production |
| 10 | No proof law is in force | Regulatory evidence | No currentness check | Cannot verify recency | None | "Not legal advice" | Add currentness API | Future production |
| 11 | No external doc auth | Certificate verification | No API call | Cannot verify issuing authority | Medium | "Not externally authenticated" | Add chamber/PSW API | Future production |
| 12 | No PSW/TDAP/bank API | Supporting docs | No external verification | Cannot confirm authenticity | Medium | "Internal consistency only" | Add integrations | Future production |
| 13 | Internal consistency ≠ clearance | Audit result | Deterministic match only | May mislead | Medium | "Not customs clearance" | Add disclaimer | **Should fix before presentation** |
| 14 | OCR errors | PDF extraction | Errors propagate | Wrong extracted values | Low | "May require human review" | Improve OCR | Accepted capstone |
| 15 | Dense retrieval can fail | Embeddings | Exception caught | Indexing fails, RAG unavailable | Low | "Document indexing may fail" | Retry mechanism | Accepted capstone |
| 16 | Reranker degraded mode | Cross-encoder | Falls back to RRF | Less accurate ranking | Low | Not shown to user | Log warning | Accepted capstone |
| 17 | Evidence gate may reject useful passages | Evidence gate | threshold at -2.0 | Relevant content missed | Low | No indication shown | Tune threshold | Accepted capstone |
| 18 | Evidence gate may admit borderline passages | Evidence gate | Simplified checks | Weak evidence accepted | Low | Evidence status shown | Add stricter checks | Accepted capstone |
| 19 | Follow-up resolution is heuristic | Pronoun resolution | String matching only | May misresolve | Low | Not explicitly stated | Use conversation context | Accepted capstone |
| 20 | Long conversations lose context | Conversation model | Only last message checked | Older references lost | Low | Not explicitly stated | Sliding window | Future production |
| 21 | Cannot edit audited values | Chat endpoint | Explicit rejection | Must use human review | None | "Use formal review workflow" | Intentional | N/A |
| 22 | Corrected docs require re-upload | Document workflow | No in-place edit | Full re-processing needed | Low | Not explicitly stated | Add correction flow | Future production |
| 23 | Chat ≠ formal review | Chat endpoint | Explicit boundary | Cannot replace review | None | "Cannot modify audit data" | Intentional | N/A |
| 24 | Prompt injection mitigated, not impossible | RAG + routing | Keyword detection + frozen audit | Theoretically bypassable | Medium | "Mitigated" | Add LLM guardrails | Future production |
| 25 | Test fixtures ≠ real docs | Test suite | Synthetic data only | Test gap | Low | N/A | Add real doc tests | Future production |
| 26 | No official legal advice | All guidance | Explicit disclaimer | Cannot rely legally | None | "Not legal advice" | Intentional | N/A |
| 27 | No production-readiness claim | Entire system | Prototype only | Demo use only | None | "Capstone prototype" | Full audit | Future production |
| 28 | Local PG + fake providers ≠ prod | Demo environment | Local execution | Different from deployment | Low | N/A | Staging env | Future production |
| 29 | Frontend errors limited | Error handling | Catches generic errors | Some failures not specific | Low | Generic error shown | Add specific handlers | Accepted capstone |
| 30 | No load testing | Performance | Untested under load | Unknown scalability | Low | N/A | Add k6/locust | Future production |
