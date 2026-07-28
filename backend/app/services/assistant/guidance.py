from uuid import UUID, uuid4
from sqlalchemy.orm import Session

from app.schemas.assistant import GuidanceResponse, DocumentGuidanceSchema, SourceSchema
from app.schemas.compliance import ShipmentComplianceInput
from app.schemas.regulatory_evidence import EvidenceSearchRequest
from app.services.assistant.foundation import validate_pct_scope
from app.services.compliance.rule_engine import get_compliance_rule_engine
from app.services.compliance.document_requirements import collect_outstanding_documents
from app.services.regulatory.evidence_api import run_evidence_search


def get_document_explanation(doc_type: str) -> str:
    explanations = {
        "commercial_invoice": "The Commercial Invoice is the primary document used for customs declaration. It details the price, value, and quantity of the goods being sold.",
        "packing_list": "The Packing List details the exact contents of each package, box, or container in the shipment, including weights and dimensions.",
        "form_e": "Form-E is the PSW Export Declaration required for electronic customs clearance. It replaces the manual Goods Declaration.",
        "certificate_of_origin": "The Certificate of Origin is required to prove where the goods were manufactured, often necessary for preferential tariff rates under trade agreements.",
    }
    return explanations.get(doc_type.lower(), f"This document ({doc_type}) is required for compliance with export or destination policies.")


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
    
    docs = []
    
    for doc in outstanding:
        # Build search query for regulatory evidence
        query = f"why is {doc.display_name} required for {expected_product} {code} to {destination}?"
        req = EvidenceSearchRequest(
            query=query,
            pct_code=code,
            destination_country=destination,
            top_k=1
        )
        evidence_resp = run_evidence_search(db, req)
        
        citations = []
        evidence_status = "unavailable"
        
        if evidence_resp.status == "ok" and evidence_resp.results:
            res = evidence_resp.results[0]
            evidence_status = "available"
            citations.append(
                SourceSchema(
                    source_kind="regulatory",
                    display_name=res.source_document,
                    snippet=res.child_evidence_text,
                    source_document=res.source_document,
                    audit_revision_number=None,
                    status=None,
                    evidence_status="accepted",
                    page_number=res.page_number
                )
            )
            
        reason_text = " ".join(doc.reasons) if doc.reasons else "Required by configured rules."
        
        if evidence_status == "unavailable":
            reason_text += "\n\nThe indexed regulatory documents did not contain a sufficiently relevant passage for this requirement."
            
        docs.append(
            DocumentGuidanceSchema(
                document_type=doc.document_type,
                display_name=doc.display_name,
                requirement=doc.requirement,
                reason=reason_text + "\n\n" + get_document_explanation(doc.document_type),
                evidence_status=evidence_status,
                citations=citations
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
            evidence_status="baseline",
            citations=[]
        ),
        DocumentGuidanceSchema(
            document_type="packing_list",
            display_name="Packing List",
            requirement="required",
            reason=get_document_explanation("packing_list"),
            evidence_status="baseline",
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
        answer=f"For {expected_product} under PCT {code} being exported to {destination}, CACE currently expects the following documents. The document list comes from the configured CACE rules. Regulatory evidence is displayed only when a sufficiently relevant source was found.\n\nThis guidance covers only the five PCT codes supported by this prototype. It is not official customs or legal advice.",
        limitations=[
            "This assistant is running in single-user prototype mode. Account-based authorization and multi-user document isolation are not implemented.",
            "This guidance covers only the five PCT codes supported by this prototype. It is not official customs or legal advice."
        ]
    )
