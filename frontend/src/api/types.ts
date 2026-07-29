export type ComplianceStatus =
  | "passed"
  | "failed"
  | "manual_review"
  | "not_applicable";

export type WorkflowStatus =
  | "created"
  | "running"
  | "awaiting_human_review"
  | "resuming"
  | "completed"
  | "rejected"
  | "failed";

export interface DocumentUploadResponse {
  document_id: string;
  original_filename: string;
  stored_filename: string;
  size_bytes: number;
  status: string;
}

export interface DocumentExtractionResponse {
  document_id: string;
  status: string;
  page_count: number;
  character_count: number;
}

export interface DocumentMetadata {
  document_id: string;
  original_filename: string;
  stored_filename: string;
  file_extension: string;
  content_type: string;
  size_bytes: number;
  status: string;
  page_count: number | null;
  character_count: number | null;
  extracted_at: string | null;
  structured_extraction_status: string;
  structured_extracted_at: string | null;
  uploaded_at: string;
}

export interface ExtractedField<T = unknown> {
  value: T | null;
  source_document_id?: string;
  source_page?: number | null;
  extraction_method?: string;
  confidence?: string | number;
  ocr_confidence?: string | number | null;
  validation_status?: string;
  validation_note?: string;
  derivation_method?: string | null;
}

export interface ComplianceCheck {
  check_id: string;
  check_name: string;
  status: ComplianceStatus;
  message: string;
  pct_code?: string | null;
  required_document?: string | null;
  source_document?: string | null;
  sro_number?: string | null;
  source_url?: string | null;
  source_page?: number | null;
  issuing_authority?: string | null;
  validation_status?: string | null;
}

export interface CrossDocumentCheck {
  check_id: string;
  check_name: string;
  status: ComplianceStatus;
  message: string;
  invoice_source_page?: number | null;
  packing_list_source_page?: number | null;
}

export interface ComplianceResult {
  overall_status: ComplianceStatus;
  checks?: ComplianceCheck[];
  executable_rule_checks?: ComplianceCheck[];
}

export interface ShipmentItemResult {
  item_reference: string;
  product_name: string | null;
  pct_code: string | null;
  match_status: string;
  match_strategy: string | null;
  match_note: string;
  status: ComplianceStatus;
  item_checks: CrossDocumentCheck[];
  compliance: ComplianceResult | null;
  fields_requiring_manual_review: string[];
}

export interface OutstandingDocument {
  document_type: string;
  display_name: string;
  requirement: "required" | "conditional";
  reasons: string[];
  sources: string[];
}

export interface MultiLineShipmentResponse {
  /** Strict customs verdict: outstanding paperwork still blocks submission. */
  overall_status: ComplianceStatus;
  is_compliant: boolean;
  rule_data_version: string;
  commercial_invoice_document_id: string;
  packing_list_document_id: string;
  invoice: Record<string, ExtractedField | unknown[]>;
  packing_list: Record<string, ExtractedField | unknown[]>;
  page_reviews: Array<Record<string, unknown>>;
  shipment_level_checks: CrossDocumentCheck[];
  items: ShipmentItemResult[];
  fields_requiring_manual_review: string[];
  supporting_documents: Array<Record<string, unknown>>;
  /** Verdict on the two uploaded documents alone. */
  document_review_status: ComplianceStatus;
  outstanding_documents: OutstandingDocument[];
}

export interface MultiLineShipmentRequest {
  commercial_invoice_document_id: string;
  packing_list_document_id: string;
  shipment_date?: string | null;
  letter_of_credit_date?: string | null;
  additional_uploaded_document_types: string[];
  supporting_documents: Array<{
    document_type: string;
    document_id: string;
  }>;
}

export interface WorkflowStatusResponse {
  workflow_id: string;
  thread_id: string;
  status: WorkflowStatus;
  current_node: string | null;
  deterministic_status: string | null;
  requires_human_review: boolean;
  rule_data_version: string | null;
  vector_index_version: string | null;
  final_report: Record<string, unknown> | null;
  errors: unknown[] | null;
  created_at: string | null;
  completed_at: string | null;
}

export interface DisputedFieldDetail {
  field_path: string;
  document_type: string;
  document_id: string | null;
  page: number | null;
  value: unknown;
  confidence: number | null;
  extraction_method: string | null;
}

export interface ReviewTaskResponse {
  task_id: string;
  workflow_id: string;
  status: string;
  reason: string;
  disputed_fields: unknown[];
  requested_actions: unknown[];
  evidence: unknown[];
  deterministic_status: string | null;
  created_at: string | null;
  review_task_id: string | null;
  revision_number: number | null;
  reason_code: string | null;
  title: string | null;
  plain_language_question: string | null;
  disputed_field_details: DisputedFieldDetail[];
  affected_check_ids: string[];
}

export interface SubmitCorrectionRequest {
  action: "confirm_extracted_value" | "correct_extracted_value";
  field_path: string;
  original_value?: unknown;
  corrected_value: unknown;
  reviewer_reference: string;
  reason: string;
  source?: string;
}

export interface AuditEvent {
  event_type: string;
  node_name: string | null;
  actor_type: string;
  actor_reference: string | null;
  new_state_hash: string | null;
  event_payload: Record<string, unknown> | null;
  created_at: string | null;
}

export interface EvidenceResult {
  child_evidence_text: string;
  parent_evidence_text: string;
  source_document: string;
  source_path: string;
  source_url: string | null;
  sro_number: string | null;
  page_number: number | null;
  section: string | null;
  issuing_authority: string | null;
  effective_date: string | null;
  legal_cutoff_date: string | null;
  validation_status: string;
  rule_data_version: string;
  cross_encoder_score: number;
}

export interface EvidenceSearchResponse {
  status: "ok" | "evidence_not_found";
  query: string;
  normalized_query: string;
  result_count: number;
  embedding_model: string;
  reranker_model: string;
  vector_index_version: string;
  retrieval_mode: string;
  degraded_mode: boolean;
  retrieval_ms: number;
  results: EvidenceResult[];
}

export interface ShipmentSearchResponse {
  status: "ok" | "no_shipments_indexed";
  retrieval_mode: string;
  query: string;
  result_count: number;
  results: Array<{
    workflow_id: string;
    score: number;
    summary: string;
  }>;
}

export interface TrackedDocument {
  id: string;
  name: string;
  role: "commercial_invoice" | "packing_list" | "supporting_document";
  sizeBytes: number;
  uploadedAt: string;
  status: string;
  pageCount?: number;
  characterCount?: number;
  complianceStatus?: ComplianceStatus;
}

export interface ShipmentChatRequest {
  conversation_id?: string | null;
  question: string;
  technical_detail?: boolean;
}

export interface ChatSource {
  source_kind: string;
  display_name?: string;
  document_type?: string;
  document_name?: string;
  page_number?: number;
  snippet?: string;
  document_id?: string;
  audit_revision_number?: number;
  status?: string;
  source_document?: string;
  evidence_status?: string;
  source_type?: string;
}

export interface ChatResponse {
  conversation_id: string;
  message_id: string;
  mode: string;
  answer_type?: string;
  answer: string;
  audit_status?: string;
  audit_revision_number?: number;
  sources: ChatSource[];
  limitations: string[];
  suggested_questions: string[];
}

export interface GuidanceRequest {
  conversation_id?: string | null;
  product: string;
  pct_code: string;
  destination: string;
  planned_shipment_date?: string | null;
  question?: string;
}

export type EvidenceClass =
  | "direct_evidence"
  | "indirect_support"
  | "configured_rule_only"
  | "evidence_unavailable"
  | "conflicting_evidence";

export interface GuidanceCitation extends ChatSource {
  source_kind_label?: string;
  is_official?: boolean;
  issuing_authority?: string | null;
  section?: string | null;
  sro_number?: string | null;
  source_url?: string | null;
  referenced_official_source?: string | null;
}

export interface DocumentGuidanceSchema {
  document_type: string;
  display_name: string;
  requirement: string;
  reason: string;
  evidence_status: string;
  evidence_class: EvidenceClass;
  summary: string;
  preparation_status: string;
  rule_sources: string[];
  citations: GuidanceCitation[];
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  sources?: unknown[] | null;
}

export interface ConversationResponse {
  id: string;
  shipment_id?: string | null;
  mode: string;
  messages: ConversationMessage[];
}

export interface SupportedProduct {
  pct_code: string;
  display_pct_code: string;
  product_name: string;
  tariff_description: string;
  textile_category: string;
  tariff_source_page: number | null;
}

export interface SupportedProductsResponse {
  products: SupportedProduct[];
}

export interface RegulatoryChatRequest {
  question: string;
  conversation_id?: string | null;
  pct_code?: string | null;
  destination?: string | null;
  source_document?: string | null;
  top_k?: number;
}

export interface RegulatoryCitation {
  title: string;
  source_kind: string;
  source_kind_label: string;
  is_official: boolean;
  issuing_authority?: string | null;
  page_number?: number | null;
  section?: string | null;
  publication_date?: string | null;
  effective_date?: string | null;
  corpus_snapshot_date?: string | null;
  accepted_passage: string;
  evidence_status: string;
  source_url?: string | null;
  sro_number?: string | null;
  referenced_official_source?: string | null;
}

export interface ChecklistDocument {
  display_name: string;
  requirement: "required" | "conditional";
  condition?: string | null;
}

export interface ProductCandidate {
  pct_code: string;
  product_name: string;
}

export interface RegulatoryChatResponse {
  conversation_id: string;
  message_id: string;
  mode: string;
  answer: string;
  intent: string;
  evidence_status: string;
  evidence_scope: string;
  answer_mode: string;
  required_documents: ChecklistDocument[];
  conditional_documents: ChecklistDocument[];
  product_candidates: ProductCandidate[];
  interpreted_as: Record<string, string>;
  resolved_product?: string | null;
  resolved_pct_code?: string | null;
  destination?: string | null;
  sources: RegulatoryCitation[];
  limitations: string[];
  supported_compliance_scope: string[];
  informational_only: boolean;
  suggested_questions: string[];
}

export interface GuidanceResponse {
  conversation_id: string;
  message_id: string;
  mode: string;
  supported_scope: boolean;
  pct_code?: string | null;
  product?: string | null;
  destination?: string | null;
  planned_shipment_date?: string | null;
  documents: DocumentGuidanceSchema[];
  limitations: string[];
  answer?: string | null;
}


