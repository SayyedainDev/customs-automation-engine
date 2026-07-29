import re
from uuid import UUID, uuid4
from sqlalchemy.orm import Session

from app.schemas.assistant import GuidanceResponse, DocumentGuidanceSchema, SourceSchema
from app.schemas.compliance import ShipmentComplianceInput
from app.services.assistant.foundation import validate_pct_scope
from app.services.compliance.rule_engine import get_compliance_rule_engine
from app.services.compliance.document_requirements import collect_outstanding_documents
from app.services.regulatory.retrieval import ScoredEvidence, search_regulatory_evidence
from app.services.regulatory.source_kinds import (
    resolve_source_kind,
    is_official,
    referenced_official_source,
    source_kind_label,
)

# Evidence classifications reported per requirement.
DIRECT_EVIDENCE = "direct_evidence"
INDIRECT_SUPPORT = "indirect_support"
CONFIGURED_RULE_ONLY = "configured_rule_only"
EVIDENCE_UNAVAILABLE = "evidence_unavailable"
CONFLICTING_EVIDENCE = "conflicting_evidence"

EVIDENCE_CLASS_NOTES = {
    DIRECT_EVIDENCE: (
        "A retrieved passage names this document directly."
    ),
    INDIRECT_SUPPORT: (
        "The retrieved passage is about this product and destination but does "
        "not name this document, so it supports the requirement in context "
        "rather than proving it."
    ),
    CONFIGURED_RULE_ONLY: (
        "This requirement comes from a configured CACE rule. The indexed corpus "
        "did not return a sufficiently relevant passage for it."
    ),
    EVIDENCE_UNAVAILABLE: (
        "No supporting passage was found in the indexed corpus and no rule "
        "source was recorded for this requirement."
    ),
    CONFLICTING_EVIDENCE: (
        "Retrieved passages disagree about whether this document is required. "
        "Treat this as unresolved and check the cited sources."
    ),
}

#: Terms that make a passage genuinely *about* a given document, rather than
#: merely about the same product. Used to tell direct proof from context.
_DOCUMENT_TERMS = {
    "form_e": ("form-e", "form e", "e-form", "export declaration"),
    "certificate_of_origin": ("certificate of origin", "origin certificate", "coo"),
    "sbp_deposit_proof": ("security deposit", "deposit", "1%", "one per cent"),
    "sbp_confirmation": ("state bank", "sbp", "confirmation"),
    "irrevocable_letter_of_credit": ("letter of credit", "irrevocable", "l/c"),
    "phytosanitary_certificate": ("phytosanitary", "plant quarantine"),
    "import_permit": ("import permit", "permit"),
    "product_permit": ("permit",),
    "product_licence": ("licence", "license"),
    "product_certificate": ("certificate",),
    "product_approval": ("approval",),
    "goods_declaration": ("goods declaration", "gd"),
    "bill_of_lading": ("bill of lading",),
    "export_contract": ("export contract", "contract"),
    "commercial_invoice": ("commercial invoice", "invoice"),
    "packing_list": ("packing list",),
}

_NEGATION = re.compile(
    r"\b(not\s+required|no\s+certificate\s+required|exempt|exemption|"
    r"certificate_required=false|not\s+applicable)\b",
    re.IGNORECASE,
)

#: Rule messages phrased as an absence. Pre-upload nothing has been compared
#: against anything, so "missing" is a claim the checklist has not earned.
_MISSING_PHRASES = (
    re.compile(r"^missing required document:\s*(.+?)\.?$", re.IGNORECASE),
    re.compile(
        r"^(?P<rule>.+?):\s*the required document '(?P<doc>[^']+)' is missing\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<rule>.+?):\s*the destination-specific document is missing\.?$",
        re.IGNORECASE,
    ),
)


def rewrite_pre_upload_reason(message: str, display_name: str) -> str:
    """Restate an absence-phrased rule message as paperwork to prepare.

    The compliance engine is untouched: it still reports the same failure for a
    shipment whose documents have been compared. This rewrite applies only to
    the pre-submission checklist, where no upload has happened yet and calling
    a document "missing" states a comparison that was never made.
    """
    text = (message or "").strip()
    for pattern in _MISSING_PHRASES:
        match = pattern.match(text)
        if not match:
            continue
        groups = match.groupdict()
        rule = groups.get("rule")
        if rule:
            return f"{rule.strip()}: {display_name} is a document to prepare for this export."
        return f"{display_name} is a document to prepare for this export."
    return text


def _mentions_document(text: str, document_type: str) -> bool:
    lowered = (text or "").casefold()
    terms = _DOCUMENT_TERMS.get(document_type)
    if not terms:
        terms = (document_type.replace("_", " "),)
    return any(term in lowered for term in terms)


def _contradicts(results: list[ScoredEvidence], document_type: str) -> bool:
    """Whether the passages that name this document disagree about it.

    Only counted when at least one passage names the document and asserts it is
    not required while another names it without that negation.
    """
    negated = 0
    affirmed = 0
    for item in results:
        if not _mentions_document(item.chunk.text, document_type):
            continue
        if _NEGATION.search(item.chunk.text):
            negated += 1
        else:
            affirmed += 1
    return negated > 0 and affirmed > 0


def _to_source_schema(item: ScoredEvidence) -> SourceSchema:
    chunk = item.chunk
    kind = resolve_source_kind(chunk)
    return SourceSchema(
        source_kind="regulatory",
        display_name=chunk.source_document,
        snippet=chunk.text,
        source_document=chunk.source_document,
        audit_revision_number=None,
        status=None,
        evidence_status="accepted",
        page_number=chunk.page_number,
        source_type=kind,
        # Extra provenance carried through SourceSchema's permissive config so
        # the console can label a curated summary honestly.
        source_kind_label=source_kind_label(kind),
        is_official=is_official(kind),
        issuing_authority=chunk.issuing_authority,
        section=chunk.section,
        sro_number=chunk.sro_number,
        source_url=chunk.source_url,
        referenced_official_source=referenced_official_source(chunk, kind),
    )


def get_document_explanation(doc_type: str) -> str:
    """Return a short, exporter-friendly reason for the checklist document.

    These are explanations of why CACE asks for a document, not new legal
    requirements.  The configured rule and its accepted source remain the
    authority; this text only translates the purpose into everyday language.
    """
    key = doc_type.casefold().replace("-", "_").replace(" ", "_")
    explanations = {
        "commercial_invoice": (
            "It shows what is being sold, who is buying it, the price, and the "
            "total value."
        ),
        "packing_list": (
            "It shows how the goods are packed, including the packages, "
            "quantities, and weights."
        ),
        "form_e": (
            "It records the export declaration submitted through PSW for the "
            "customs process."
        ),
        "form_e_psw": (
            "It records the export declaration submitted through PSW for the "
            "customs process."
        ),
        "psw_export_declaration": (
            "It records the export declaration submitted through PSW for the "
            "customs process."
        ),
        "certificate_of_origin": (
            "It shows where the goods were made. The destination or a trade "
            "scheme may require it."
        ),
        "coo": (
            "It shows where the goods were made. The destination or a trade "
            "scheme may require it."
        ),
    }
    return explanations.get(
        key,
        f"It provides information needed for this export and destination.",
    )


def generate_pre_submission_guidance(
    db: Session,
    product: str,
    pct_code: str,
    destination: str,
    planned_shipment_date: str | None = None,
    conversation_id: UUID | None = None
) -> GuidanceResponse:
    
    msg_id = uuid4()
    conv_id = conversation_id or uuid4()
    
    is_valid, msg, code, expected_product = validate_pct_scope(pct_code, product)
    
    if not is_valid:
        return GuidanceResponse(
            conversation_id=conv_id,
            message_id=msg_id,
            supported_scope=False,
            pct_code=code,
            product=product,
            destination=destination,
            answer=msg,
            limitations=[
                "This assistant is running in single-user prototype mode. Account-based authorization and multi-user document isolation are not implemented."
            ]
        )
        
    if not destination:
        return GuidanceResponse(
            conversation_id=conv_id,
            message_id=msg_id,
            supported_scope=True,
            pct_code=code,
            product=expected_product,
            destination=destination,
            answer="Please provide a destination country for this export.",
            limitations=[
                "This assistant is running in single-user prototype mode. Account-based authorization and multi-user document isolation are not implemented."
            ]
        )
        
    engine = get_compliance_rule_engine()
    shipment = ShipmentComplianceInput(
        product_name=expected_product,
        pct_code=code,
        destination_country=destination,
        uploaded_document_types=[]
    )
    
    response = engine.check(shipment)
    
    all_checks = response.checks + response.executable_rule_checks
    outstanding = collect_outstanding_documents(all_checks)
    
    # Retrieve first for every requirement, then classify. The second pass is
    # what stops one generic product passage from being shown as direct proof
    # against six different documents: a passage reused across requirements
    # that does not name the document is context, not evidence.
    retrieved: dict[str, list[ScoredEvidence]] = {}
    for doc in outstanding:
        query = (
            f"{doc.display_name} {doc.document_type.replace('_', ' ')} "
        )
        # Product, PCT and destination are already enforced by the structured
        # retrieval arguments below. Repeating them in the free-text query can
        # swamp the document name and select a sibling passage about the same
        # product (for example the SBP-deposit sentence instead of the
        # immediately following letter-of-credit sentence). Keep the text
        # focused on the document whose direct evidence is being requested.
        output = search_regulatory_evidence(
            db,
            query=query,
            pct_code=code,
            destination_country=destination,
            top_k=3,
        )
        retrieved[doc.document_type] = output.results if output.status == "ok" else []

    usage_count: dict[str, int] = {}
    for results in retrieved.values():
        for item in results:
            usage_count[item.chunk.chunk_id] = usage_count.get(item.chunk.chunk_id, 0) + 1

    docs = []

    for doc in outstanding:
        results = retrieved[doc.document_type]
        direct = [
            item
            for item in results
            if _mentions_document(item.chunk.text, doc.document_type)
        ]
        chosen = direct[:1] or results[:1]

        if not chosen:
            evidence_class = CONFIGURED_RULE_ONLY if doc.sources else EVIDENCE_UNAVAILABLE
        elif direct and _contradicts(direct, doc.document_type):
            evidence_class = CONFLICTING_EVIDENCE
        elif direct:
            evidence_class = DIRECT_EVIDENCE
        elif usage_count.get(chosen[0].chunk.chunk_id, 0) > 1:
            evidence_class = INDIRECT_SUPPORT
        else:
            evidence_class = INDIRECT_SUPPORT

        citations = [_to_source_schema(item) for item in chosen] if chosen else []
        evidence_status = "available" if evidence_class == DIRECT_EVIDENCE else (
            "contextual" if chosen else "unavailable"
        )

        reasons = [
            rewrite_pre_upload_reason(reason, doc.display_name) for reason in doc.reasons
        ]
        summary = reasons[0] if reasons else (
            f"{doc.display_name} is a document to prepare for this export."
        )
        reason_text = " ".join(reasons) if reasons else summary
        reason_text += "\n\n" + EVIDENCE_CLASS_NOTES[evidence_class]

        docs.append(
            DocumentGuidanceSchema(
                document_type=doc.document_type,
                display_name=doc.display_name,
                requirement=doc.requirement,
                reason=reason_text + "\n\n" + get_document_explanation(doc.document_type),
                summary=summary,
                evidence_status=evidence_status,
                evidence_class=evidence_class,
                preparation_status="to_prepare",
                rule_sources=list(doc.sources),
                citations=citations,
            )
        )

    # We must also include basic documents which are not flagged as "outstanding" if they are part of the baseline?
    # Wait, INPUT_DOCUMENT_TYPES like "commercial_invoice" and "packing_list" are excluded from `outstanding` by `is_outstanding_document_check`!
    # The prompt says: "for cotton knitted T-shirts... CACE expects: 1. Commercial Invoice 2. Packing List 3. Form-E 4. Certificate of Origin"
    # We should artificially inject Invoice and Packing List since they are the bedrock input documents.
    
    basic_docs = [
        DocumentGuidanceSchema(
            document_type="commercial_invoice",
            display_name="Commercial Invoice",
            requirement="required",
            reason=get_document_explanation("commercial_invoice"),
            summary="The commercial invoice is one of the two documents CACE reviews.",
            evidence_status="baseline",
            evidence_class=CONFIGURED_RULE_ONLY,
            preparation_status="to_prepare",
            rule_sources=["TIPP Customs Clearance Procedure [Export] [Web-Based]"],
            citations=[]
        ),
        DocumentGuidanceSchema(
            document_type="packing_list",
            display_name="Packing List",
            requirement="required",
            reason=get_document_explanation("packing_list"),
            summary="The packing list is one of the two documents CACE reviews.",
            evidence_status="baseline",
            evidence_class=CONFIGURED_RULE_ONLY,
            preparation_status="to_prepare",
            rule_sources=["TIPP Customs Clearance Procedure [Export] [Web-Based]"],
            citations=[]
        )
    ]
    
    return GuidanceResponse(
        conversation_id=conv_id,
        message_id=msg_id,
        supported_scope=True,
        pct_code=code,
        product=expected_product,
        destination=destination,
        documents=basic_docs + docs,
        answer=f"For {expected_product} under PCT {code} being exported to {destination}, CACE currently expects the following documents. The document list comes from the configured CACE rules. Regulatory evidence is displayed only when a sufficiently relevant source was found.\n\nThis guidance covers only the textile PCT codes configured in this prototype. It is not official customs or legal advice.",
        limitations=[
            "This assistant is running in single-user prototype mode. Account-based authorization and multi-user document isolation are not implemented.",
            "This guidance covers only the textile PCT codes configured in this prototype. It is not official customs or legal advice."
        ]
    )
