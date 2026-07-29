from app.services.assistant.guidance import generate_pre_submission_guidance

def test_guidance_unsupported_code(isolated_database):
    from sqlalchemy.orm import Session
    db = Session(isolated_database)
    
    resp = generate_pre_submission_guidance(
        db, product="Some Product", pct_code="6203.4200", destination="China"
    )
    assert resp.supported_scope is False
    assert "CACE currently supports only five textile PCT codes" in resp.answer

def test_guidance_conflict(isolated_database):
    from sqlalchemy.orm import Session
    db = Session(isolated_database)
    
    resp = generate_pre_submission_guidance(
        db, product="Cotton yarn", pct_code="61091000", destination="China"
    )
    assert resp.supported_scope is False
    assert "inconsistent" in resp.answer

def test_guidance_missing_destination(isolated_database):
    from sqlalchemy.orm import Session
    db = Session(isolated_database)
    
    resp = generate_pre_submission_guidance(
        db, product="Cotton knitted T-shirts", pct_code="61091000", destination=""
    )
    assert resp.supported_scope is True
    assert "Please provide a destination" in resp.answer
    assert not resp.documents

def test_guidance_successful(isolated_database):
    from sqlalchemy.orm import Session
    db = Session(isolated_database)
    
    resp = generate_pre_submission_guidance(
        db, product="Cotton knitted T-shirts", pct_code="61091000", destination="China"
    )
    
    assert resp.supported_scope is True
    assert "For Cotton knitted T-shirts under PCT 61091000" in resp.answer
    assert len(resp.documents) >= 4
    
    doc_types = [d.document_type for d in resp.documents]
    assert "commercial_invoice" in doc_types
    assert "packing_list" in doc_types
    assert "form_e" in doc_types
    assert "certificate_of_origin" in doc_types
    
    # Honest reporting when the corpus returns nothing for a requirement: the
    # requirement is still shown, classified as coming from the configured rule
    # rather than from retrieved evidence.
    coo = next(d for d in resp.documents if d.document_type == "certificate_of_origin")
    assert coo.evidence_class == "configured_rule_only"
    assert coo.citations == []
    assert "did not return a sufficiently relevant passage" in coo.reason
    # Pre-upload the checklist describes paperwork to obtain, never absence.
    assert coo.preparation_status == "to_prepare"
    assert "Missing required document" not in coo.reason
