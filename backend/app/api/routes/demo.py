"""Built-in sample shipment loader for interactive demos and evaluation.

Copies curated synthetic trade packets from ``synthetic_factory/`` into the
active upload directory, persists real ``DocumentUploadRecord`` rows, and runs
the real PDF text extraction service so reviewers can explore the full
compliance, PDF provenance, cascading duty, and LangGraph HITL workflow without
preparing custom Pakistani export PDFs manually.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db_session
from app.models.documents import DocumentUploadRecord
from app.schemas.documents import DocumentUploadResponse
from app.services import document_service
from app.services.document_service import extract_uploaded_pdf

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SYNTHETIC_FACTORY_DIR = PROJECT_ROOT / "synthetic_factory"

router = APIRouter(prefix="/api/v1/demo", tags=["Demo Samples"])


@dataclass(frozen=True)
class _SampleSupportingSpec:
    document_type: str
    relative_path: str
    filename: str


@dataclass(frozen=True)
class _SampleShipmentSpec:
    key: str
    title: str
    summary: str
    expected_outcome: str
    shipment_date: str
    invoice_path: str
    invoice_filename: str
    packing_list_path: str
    packing_list_filename: str
    supporting: tuple[_SampleSupportingSpec, ...] = ()


_SAMPLES: dict[str, _SampleShipmentSpec] = {
    "clean_cotton_tshirts": _SampleShipmentSpec(
        key="clean_cotton_tshirts",
        title="Clean Cotton T-Shirts Export (China CPFTA)",
        summary=(
            "Matched commercial invoice, packing list, Form-E, and Certificate "
            "of Origin for PCT 6109.1000 — demonstrates a clean PASS verdict "
            "and FY2026-27 cascading duty breakdown."
        ),
        expected_outcome="passed",
        shipment_date="2026-06-29",
        invoice_path="clean_cotton_tshirts/synthetic_commercial_invoice_text.pdf",
        invoice_filename="sample_clean_cotton_tshirts_invoice.pdf",
        packing_list_path="clean_cotton_tshirts/synthetic_packing_list_text.pdf",
        packing_list_filename="sample_clean_cotton_tshirts_packing_list.pdf",
        supporting=(
            _SampleSupportingSpec(
                document_type="form_e",
                relative_path=(
                    "clean_cotton_tshirts/supporting/"
                    "supporting_form_e_or_psw_export_declaration_text.pdf"
                ),
                filename="sample_clean_cotton_tshirts_form_e.pdf",
            ),
            _SampleSupportingSpec(
                document_type="certificate_of_origin",
                relative_path=(
                    "clean_cotton_tshirts/supporting/"
                    "supporting_certificate_of_origin_text.pdf"
                ),
                filename="sample_clean_cotton_tshirts_coo.pdf",
            ),
        ),
    ),
    "missing_documents_hitl": _SampleShipmentSpec(
        key="missing_documents_hitl",
        title="Pending Bank Form-E & COO (HITL Upload)",
        summary=(
            "Clean commercial invoice and packing list for PCT 6109.1000 sent "
            "before attaching Form-E and Certificate of Origin — demonstrates "
            "sound document triage vs. pending external paperwork and inline "
            "HITL supporting-document upload."
        ),
        expected_outcome="failed",
        shipment_date="2026-06-29",
        invoice_path="clean_cotton_tshirts/synthetic_commercial_invoice_text.pdf",
        invoice_filename="sample_pending_paperwork_invoice.pdf",
        packing_list_path="clean_cotton_tshirts/synthetic_packing_list_text.pdf",
        packing_list_filename="sample_pending_paperwork_packing_list.pdf",
    ),
    "error_quantity_mismatch": _SampleShipmentSpec(
        key="error_quantity_mismatch",
        title="Quantity Mismatch Export (Document Conflict Gate)",
        summary=(
            "Commercial invoice (100 pcs) and packing list (99 pcs) disagree — "
            "demonstrates high port-hold risk triage, PDF provenance "
            "highlighting, and fail-closed HITL protection against overwriting "
            "confirmed digital source contradictions."
        ),
        expected_outcome="failed",
        shipment_date="2026-06-16",
        invoice_path="error_quantity_mismatch/synthetic_commercial_invoice_text.pdf",
        invoice_filename="sample_quantity_mismatch_invoice.pdf",
        packing_list_path="error_quantity_mismatch/synthetic_packing_list_text.pdf",
        packing_list_filename="sample_quantity_mismatch_packing_list.pdf",
    ),
}


class DemoSampleDescriptor(BaseModel):
    key: str
    title: str
    summary: str
    expected_outcome: str
    shipment_date: str
    supporting_document_types: list[str]


class DemoSampleListResponse(BaseModel):
    samples: list[DemoSampleDescriptor]


class LoadedDemoSupportingDocument(BaseModel):
    document_type: str
    document: DocumentUploadResponse


class DemoSampleLoadResponse(BaseModel):
    sample: DemoSampleDescriptor
    commercial_invoice: DocumentUploadResponse
    packing_list: DocumentUploadResponse
    supporting_documents: list[LoadedDemoSupportingDocument]
    shipment_date: str


def _ensure_enabled() -> None:
    if not get_settings().enable_demo_samples:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demo sample endpoints are disabled in this environment.",
        )


def _descriptor(spec: _SampleShipmentSpec) -> DemoSampleDescriptor:
    return DemoSampleDescriptor(
        key=spec.key,
        title=spec.title,
        summary=spec.summary,
        expected_outcome=spec.expected_outcome,
        shipment_date=spec.shipment_date,
        supporting_document_types=[item.document_type for item in spec.supporting],
    )


def _ingest_sample_pdf(
    db: Session,
    *,
    source_path: Path,
    original_filename: str,
) -> DocumentUploadResponse:
    if not source_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sample fixture file is unavailable: {source_path.name}",
        )
    content = source_path.read_bytes()
    upload_dir = Path(document_service.UPLOAD_DIRECTORY)
    upload_dir.mkdir(parents=True, exist_ok=True)

    doc_id = uuid4()
    stored_filename = f"{doc_id}.pdf"
    stored_path = upload_dir / stored_filename
    stored_path.write_bytes(content)

    record = DocumentUploadRecord(
        id=doc_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_extension=".pdf",
        mime_type="application/pdf",
        size_bytes=len(content),
        status="uploaded",
    )
    db.add(record)
    db.commit()
    extract_uploaded_pdf(db=db, document_id=doc_id)
    db.refresh(record)
    return DocumentUploadResponse(
        document_id=doc_id,
        original_filename=record.original_filename,
        stored_filename=record.stored_filename,
        size_bytes=record.size_bytes,
        status=record.status,
    )


@router.get("/samples", response_model=DemoSampleListResponse)
def list_demo_samples() -> DemoSampleListResponse:
    _ensure_enabled()
    return DemoSampleListResponse(
        samples=[_descriptor(spec) for spec in _SAMPLES.values()]
    )


@router.post(
    "/samples/{sample_key}/load",
    response_model=DemoSampleLoadResponse,
    status_code=status.HTTP_201_CREATED,
)
def load_demo_sample(
    sample_key: str,
    db: Session = Depends(get_db_session),
) -> DemoSampleLoadResponse:
    _ensure_enabled()
    spec = _SAMPLES.get(sample_key)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown sample shipment key: {sample_key}",
        )

    invoice = _ingest_sample_pdf(
        db,
        source_path=SYNTHETIC_FACTORY_DIR / spec.invoice_path,
        original_filename=spec.invoice_filename,
    )
    packing = _ingest_sample_pdf(
        db,
        source_path=SYNTHETIC_FACTORY_DIR / spec.packing_list_path,
        original_filename=spec.packing_list_filename,
    )
    supporting = [
        LoadedDemoSupportingDocument(
            document_type=item.document_type,
            document=_ingest_sample_pdf(
                db,
                source_path=SYNTHETIC_FACTORY_DIR / item.relative_path,
                original_filename=item.filename,
            ),
        )
        for item in spec.supporting
    ]
    return DemoSampleLoadResponse(
        sample=_descriptor(spec),
        commercial_invoice=invoice,
        packing_list=packing,
        supporting_documents=supporting,
        shipment_date=spec.shipment_date,
    )
