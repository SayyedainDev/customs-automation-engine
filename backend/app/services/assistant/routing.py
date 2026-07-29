import re
from typing import Literal

QuestionRoute = Literal[
    "pre_submission_guidance",
    "shipment_document_fact",
    "audit_result",
    "regulatory_guidance",
    "combined_shipment_and_regulation",
    "audit_history",
    "out_of_scope"
]

def _classify_by_keywords(question: str) -> QuestionRoute | None:
    """Fast, deterministic classification for obvious user intents."""
    q = question.lower().strip()
    
    # Audit History
    if any(phrase in q for phrase in ["previous audit", "audit history", "past decisions", "who reviewed", "manual review history"]):
        return "audit_history"
        
    # Audit Results
    if any(phrase in q for phrase in [
        "why did it fail",
        "why is it rejected",
        "what is wrong",
        "missing document",
        "manual review",
        "why did it pass",
        "did the shipment pass",
        "which documents were checked",
        "failed",
    ]):
        return "audit_result"
        
    # Pre-submission guidance
    if any(phrase in q for phrase in ["want to export", "what documents should i prepare", "what do i need for"]):
        return "pre_submission_guidance"
        
    # Regulatory
    if any(phrase in q for phrase in [
        "sro",
        "export policy order",
        "tipp",
        "trade agreement",
        "law says",
        "regulatory evidence",
    ]):
        return "regulatory_guidance"
        
    # Combined shipment and regulation
    if ("does my" in q or "is my" in q) and ("satisfy" in q or "match" in q or "required" in q) and ("form-e" in q or "certificate" in q or "document" in q):
        return "combined_shipment_and_regulation"
        
    # Out of scope - explicit system-changing requests or non-customs requests
    if any(phrase in q for phrase in [
        "change the quantity",
        "mark this shipment approved",
        "ignore previous instructions",
        "write a poem",
        "write python",
        "write javascript",
        "debug my code",
        "coding question",
    ]):
        return "out_of_scope"
        
    # Shipment Document Fact
    if any(phrase in q for phrase in [
        "invoice total",
        "who is the buyer",
        "quantity",
        "match",
        "invoice number",
        "weight",
        "pct code",
        "party",
    ]):
        return "shipment_document_fact"
        
    return None

def classify_question(question: str) -> QuestionRoute:
    """Route question to the correct handler, using deterministic matching or LLM fallback."""
    # Step 1: Clean basic whitespace and punctuation artifacts
    clean_q = re.sub(r'\s+', ' ', question).strip()
    
    # Step 2: Deterministic match
    route = _classify_by_keywords(clean_q)
    if route:
        return route
        
    # Step 3: LLM Fallback (Simulated as we are avoiding real LLM calls in base testing)
    # Since we can't make real Groq calls for routing easily without an API key, we will fall back to document facts
    # if it asks "what is" and regulatory if it asks "why is".
    q = clean_q.lower()
    if "why" in q and "required" in q:
        return "combined_shipment_and_regulation"
    if "why" in q:
        return "audit_result"
    if "what should i do" in q or "how to correct" in q:
        return "audit_result"
    
    # Default fallback for simple queries about the shipment
    return "shipment_document_fact"
