from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from app.core.database import get_db_session
from app.models.assistant import AssistantConversation, AssistantMessage
from app.schemas.assistant import (
    GuidanceRequest,
    GuidanceResponse,
    ShipmentChatRequest,
    ChatResponse,
)
from app.services.assistant.guidance import generate_pre_submission_guidance
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
