from uuid import uuid4
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.documents import DocumentUploadRecord
from app.models.customs_audit import CustomsAuditWorkflow
from app.models.shipment_chunks import ShipmentDocumentChunk
from app.services.assistant.shipment_indexer import index_shipment_documents

def test_index_shipment_documents(isolated_database):
    db = Session(isolated_database)
    
    # Setup workflow and documents
    workflow_id = uuid4()
    doc1_id = uuid4()
    
    workflow = CustomsAuditWorkflow(id=workflow_id, thread_id="thread-1", status="passed", invoice_document_id=doc1_id)
    doc1 = DocumentUploadRecord(
        id=doc1_id, 
        original_filename="inv.pdf", 
        stored_filename="inv.pdf",
        file_extension=".pdf",
        mime_type="application/pdf",
        size_bytes=100,
        extracted_pages=[{"page_number": 1, "text": "Invoice total is 100"}],
        structured_data={"pct_code": "61091000", "invoice_number": "INV-001"}
    )
    
    db.add(workflow)
    db.add(doc1)
    db.commit()
    
    # Run indexing
    index_shipment_documents(db, workflow_id, [(doc1_id, "commercial_invoice")])
    
    # Verify indexed chunks
    chunks = db.execute(select(ShipmentDocumentChunk).where(ShipmentDocumentChunk.shipment_id == workflow_id)).scalars().all()
    assert len(chunks) == 2 # 1 parent + 1 child
    
    parent_chunk = next(c for c in chunks if c.is_parent)
    child_chunk = next(c for c in chunks if not c.is_parent)
    
    assert parent_chunk.document_type == "commercial_invoice"
    assert parent_chunk.pct_code == "61091000"
    assert parent_chunk.invoice_number == "INV-001"
    assert parent_chunk.active is True
    
    # Test duplicate indexing does nothing (hash prevention)
    index_shipment_documents(db, workflow_id, [(doc1_id, "commercial_invoice")])
    chunks2 = db.execute(select(ShipmentDocumentChunk).where(ShipmentDocumentChunk.shipment_id == workflow_id)).scalars().all()
    assert len(chunks2) == 2
    
    # Test replacing document
    doc2_id = uuid4()
    doc2 = DocumentUploadRecord(
        id=doc2_id, 
        original_filename="inv_v2.pdf", 
        stored_filename="inv_v2.pdf",
        file_extension=".pdf",
        mime_type="application/pdf",
        size_bytes=100,
        extracted_pages=[{"page_number": 1, "text": "Invoice total is 200"}],
        structured_data={"pct_code": "61091000", "invoice_number": "INV-001"}
    )
    workflow.invoice_document_id = doc2_id
    db.add(doc2)
    db.commit()
    
    index_shipment_documents(db, workflow_id, [(doc2_id, "commercial_invoice")])
    
    # Verify old chunks are inactive, new ones are active
    all_chunks = db.execute(select(ShipmentDocumentChunk).where(ShipmentDocumentChunk.shipment_id == workflow_id)).scalars().all()
    assert len(all_chunks) == 4 # 2 old (inactive) + 2 new (active)
    
    active_chunks = [c for c in all_chunks if c.active]
    assert len(active_chunks) == 2
    assert active_chunks[0].document_id == doc2_id
    assert any("200" in c.text for c in active_chunks)
    
    inactive_chunks = [c for c in all_chunks if not c.active]
    assert len(inactive_chunks) == 2
    assert inactive_chunks[0].document_id == doc1_id
