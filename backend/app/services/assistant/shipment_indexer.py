import hashlib
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.models.documents import DocumentUploadRecord
from app.models.shipment_chunks import ShipmentDocumentChunk
from app.services.regulatory.embeddings import get_embedding_provider

logger = logging.getLogger(__name__)

def _generate_content_hash(
    document_id: UUID,
    page_number: int,
    section: str,
    text: str,
) -> str:
    """Stable content hash for a document chunk."""
    hasher = hashlib.sha256()
    hasher.update(str(document_id).encode("utf-8"))
    hasher.update(str(page_number).encode("utf-8"))
    hasher.update(section.encode("utf-8"))
    hasher.update(text.encode("utf-8"))
    return hasher.hexdigest()

def build_semantic_chunks_for_document(
    document: DocumentUploadRecord,
    shipment_id: UUID,
    workflow_id: UUID | None = None,
    document_type: str = "unknown"
) -> list[ShipmentDocumentChunk]:
    """Create parent and child chunks for an extracted document."""
    if not document.extracted_pages:
        return []

    structured_data = document.structured_data or {}
    pct_code = structured_data.get("pct_code") or structured_data.get("product_pct_code")
    if isinstance(pct_code, dict):
        pct_code = pct_code.get("value")
    invoice_number = structured_data.get("invoice_number") or structured_data.get("invoice_reference")
    if isinstance(invoice_number, dict):
        invoice_number = invoice_number.get("value")
    
    chunks = []
    
    # Simple typed parent-child chunking for the prototype.
    for page in document.extracted_pages:
        page_num = page.get("page_number", 1)
        text = page.get("text", "")
        if not text.strip():
            continue
            
        # Simplistic sectioning: split by paragraphs
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text.strip()]
            
        for p_idx, paragraph in enumerate(paragraphs):
            section_name = "general_content"
            p_lower = paragraph.lower()
            
            # Heuristics based on document type
            if document_type == "commercial_invoice":
                if "invoice" in p_lower and "no" in p_lower or "date" in p_lower:
                    section_name = "document_identity"
                elif "exporter" in p_lower or "buyer" in p_lower or "consignee" in p_lower:
                    section_name = "parties"
                elif "total" in p_lower or "amount" in p_lower or "usd" in p_lower:
                    section_name = "shipment_totals"
                else:
                    section_name = "product_line"
            elif document_type == "packing_list":
                if "packing list" in p_lower or "no" in p_lower:
                    section_name = "document_identity"
                elif "weight" in p_lower or "kg" in p_lower or "net" in p_lower or "gross" in p_lower:
                    section_name = "weight_totals"
                elif "package" in p_lower or "carton" in p_lower or "box" in p_lower:
                    section_name = "package_information"
                else:
                    section_name = "product_line"
            elif document_type in ("form_e", "psw_declaration"):
                if "declaration" in p_lower or "form e" in p_lower:
                    section_name = "declaration_identity"
                elif "exporter" in p_lower or "invoice" in p_lower:
                    section_name = "exporter_and_invoice"
                elif "origin" in p_lower or "destination" in p_lower:
                    section_name = "product_and_destination"
                else:
                    section_name = "declared_amount"
            elif document_type == "certificate_of_origin":
                if "certificate" in p_lower:
                    section_name = "certificate_identity"
                elif "exporter" in p_lower or "consignee" in p_lower:
                    section_name = "exporter_and_consignee"
                elif "origin" in p_lower or "product" in p_lower:
                    section_name = "product_and_origin"
                else:
                    section_name = "destination_and_authority"

            parent_id = uuid4()
            parent_hash = _generate_content_hash(document.id, page_num, section_name, paragraph)
            
            parent_chunk = ShipmentDocumentChunk(
                id=parent_id,
                shipment_id=shipment_id,
                workflow_id=workflow_id,
                document_id=document.id,
                document_version=1,
                document_type=document_type,
                document_name=document.original_filename,
                page_number=page_num,
                section=section_name,
                pct_code=str(pct_code) if pct_code else None,
                invoice_number=str(invoice_number) if invoice_number else None,
                source_kind="uploaded_document",
                is_parent=True,
                content_hash=parent_hash,
                active=True,
                text=paragraph,
                search_text=paragraph.lower()
            )
            chunks.append(parent_chunk)
            
            # Child chunks
            lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
            if len(lines) == 1:
                # Fallback to sentences if only one line
                import re
                lines = [s.strip() for s in re.split(r'(?<=[.!?]) +', paragraph) if s.strip()]
                
            for c_idx, child_text in enumerate(lines):
                if not child_text:
                    continue
                child_id = uuid4()
                child_hash = _generate_content_hash(document.id, page_num, f"{section_name}_child_{c_idx}", child_text)
                
                child_chunk = ShipmentDocumentChunk(
                    id=child_id,
                    shipment_id=shipment_id,
                    workflow_id=workflow_id,
                    document_id=document.id,
                    document_version=1,
                    document_type=document_type,
                    document_name=document.original_filename,
                    page_number=page_num,
                    section=section_name,
                    pct_code=str(pct_code) if pct_code else None,
                    invoice_number=str(invoice_number) if invoice_number else None,
                    source_kind="uploaded_document",
                    parent_chunk_id=parent_id,
                    is_parent=False,
                    child_index=c_idx,
                    content_hash=child_hash,
                    active=True,
                    text=child_text,
                    search_text=child_text.lower()
                )
                chunks.append(child_chunk)
        
    # Generate embeddings
    if chunks:
        embedder = get_embedding_provider()
        texts = [c.search_text for c in chunks]
        try:
            embeddings_matrix = embedder.embed(texts)
            for i, chunk in enumerate(chunks):
                chunk.embedding = [float(v) for v in embeddings_matrix[i].tolist()]
                chunk.embedding_model = embedder.model_name
                chunk.embedding_dim = embedder.dimension
        except Exception as exc:
            logger.error("Failed to generate embeddings for document %s: %s", document.id, exc)
            raise

    return chunks

def index_shipment_documents(
    db: Session,
    shipment_id: UUID,
    documents: list[tuple[UUID, str]]
) -> None:
    """Index all documents attached to a shipment.
    
    Only indexes documents that are successfully extracted.
    Invalidates old versions automatically.
    """
    for doc_id, doc_type in documents:
        doc = db.get(DocumentUploadRecord, doc_id)
        if not doc or not doc.extracted_pages:
            if doc:
                doc.indexing_status = "skipped_unchanged"
                db.add(doc)
            continue
            
        # Check if already indexed with same content
        existing = db.execute(
            select(ShipmentDocumentChunk)
            .where(
                ShipmentDocumentChunk.document_id == doc_id,
                ShipmentDocumentChunk.shipment_id == shipment_id,
                ShipmentDocumentChunk.active == True
            )
        ).scalars().first()
        
        if existing:
            doc.indexing_status = "skipped_unchanged"
            db.add(doc)
            continue
            
        # Deactivate old versions of this document type for this shipment
        stmt = (
            update(ShipmentDocumentChunk)
            .where(
                ShipmentDocumentChunk.shipment_id == shipment_id,
                ShipmentDocumentChunk.document_type == doc_type,
                ShipmentDocumentChunk.active == True,
                ShipmentDocumentChunk.document_id != doc_id
            )
            .values(active=False)
        )
        db.execute(stmt)
        
        try:
            chunks = build_semantic_chunks_for_document(
                document=doc,
                shipment_id=shipment_id,
                workflow_id=None,
                document_type=doc_type
            )
            
            if chunks:
                db.add_all(chunks)
            doc.indexing_status = "indexed"
            doc.indexing_error = None
        except Exception as exc:
            logger.error("Failed to index document %s: %s", doc_id, exc)
            doc.indexing_status = "failed"
            doc.indexing_error = str(exc)
        
        db.add(doc)
            
    db.commit()
