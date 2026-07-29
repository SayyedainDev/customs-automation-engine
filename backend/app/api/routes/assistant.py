from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from app.core.database import get_db_session
from app.models.assistant import AssistantConversation, AssistantMessage
from app.schemas.assistant import (
    GuidanceRequest,
    GuidanceResponse,
    RegulatoryChatRequest,
    RegulatoryChatResponse,
    ShipmentChatRequest,
    ChatResponse,
)
from app.services.assistant.guidance import generate_pre_submission_guidance
from app.services.assistant.regulatory_chat import (
    SUGGESTED_QUESTIONS,
    answer_regulatory_question,
)
from app.services.assistant.scopes import (
    get_knowledge_corpus_scope,
    supported_compliance_scope_labels,
)
from app.services.compliance.pct_catalog import load_pct_catalog
from app.services.assistant.shipment_assistant import answer_shipment_question


router = APIRouter(
    prefix="/api/v1/assistant",
    tags=["Assistant"],
)

@router.post("/guidance", response_model=GuidanceResponse)
def get_guidance(
    payload: GuidanceRequest,
    db: Session = Depends(get_db_session),
) -> GuidanceResponse:
    return generate_pre_submission_guidance(
        db,
        product=payload.product,
        pct_code=payload.pct_code,
        destination=payload.destination,
        planned_shipment_date=payload.planned_shipment_date,
        conversation_id=payload.conversation_id
    )

@router.post("/regulatory/chat", response_model=RegulatoryChatResponse)
def post_regulatory_chat(
    payload: RegulatoryChatRequest,
    db: Session = Depends(get_db_session),
) -> RegulatoryChatResponse:
    """Ask CACE about the indexed regulatory corpus.

    Needs no shipment, upload, audit workflow or supported PCT code. Answers are
    informational: the deterministic compliance engine still decides only the
    validated textile PCT catalog, and this endpoint never issues a verdict.
    """
    if payload.conversation_id:
        conv = db.get(AssistantConversation, payload.conversation_id)
        if conv and conv.shipment_id is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Conversation is bound to a shipment and cannot be reused for "
                    "general regulatory questions."
                ),
            )
    return answer_regulatory_question(
        db,
        question=payload.question,
        conversation_id=payload.conversation_id,
        pct_code=payload.pct_code,
        destination=payload.destination,
        source_document=payload.source_document,
        top_k=payload.top_k,
    )


@router.get("/supported-products")
def get_supported_products():
    """The deterministic PCT catalog, for the Prepare an Export form.

    The console used to hard-code the product list, which silently drifted
    from the codes the engine actually supports.
    """
    return {
        "products": [
            {
                "pct_code": p.pct_code,
                "display_pct_code": p.display_pct_code,
                "product_name": p.simple_product_name,
                "tariff_description": p.official_tariff_description,
                "textile_category": p.textile_category,
                "tariff_source_page": p.tariff_source_page,
            }
            for p in load_pct_catalog()
        ]
    }


@router.get("/regulatory/scope")
def get_regulatory_scope(db: Session = Depends(get_db_session)):
    """What the assistant can search versus what the engine can decide."""
    corpus = get_knowledge_corpus_scope(db)
    return {
        "knowledge_corpus_scope": {
            "source_documents": list(corpus.source_documents),
            "document_types": list(corpus.document_types),
            "chunk_count": corpus.chunk_count,
        },
        "deterministic_compliance_scope": supported_compliance_scope_labels(),
        "suggested_questions": list(SUGGESTED_QUESTIONS),
    }


@router.post("/shipments/{shipment_id}/chat", response_model=ChatResponse)
def post_shipment_chat(
    shipment_id: UUID,
    payload: ShipmentChatRequest,
    db: Session = Depends(get_db_session),
) -> ChatResponse:
    
    # Single-user safety boundary enforcement
    # "A request attempting to use the same conversation with another shipment must be rejected."
    if payload.conversation_id:
        conv = db.get(AssistantConversation, payload.conversation_id)
        if conv and conv.shipment_id and conv.shipment_id != shipment_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Conversation is bound to another shipment."
            )
            
    return answer_shipment_question(db, shipment_id, payload.question, payload.conversation_id)


@router.get("/shipments/{shipment_id}/suggestions")
def get_shipment_suggestions(
    shipment_id: UUID,
    db: Session = Depends(get_db_session),
):
    return {
        "suggestions": [
            "What is the invoice total?",
            "Who is the buyer?",
            "Why did this shipment fail?",
            "Which document is missing?"
        ]
    }


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db_session),
):
    conv = db.get(AssistantConversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    messages = db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.conversation_id == conversation_id)
        .order_by(AssistantMessage.created_at)
    ).scalars().all()
    
    return {
        "id": conv.id,
        "shipment_id": conv.shipment_id,
        "mode": conv.mode,
        "messages": [
            {"id": m.id, "role": m.role, "text": m.text, "sources": m.sources}
            for m in messages
        ]
    }

@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db_session),
):
    conv = db.get(AssistantConversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    db.execute(
        delete(AssistantMessage).where(AssistantMessage.conversation_id == conversation_id)
    )
    
    db.delete(conv)
    db.commit()
