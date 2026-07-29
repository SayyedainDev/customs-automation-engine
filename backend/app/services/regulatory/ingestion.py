"""Idempotent ingestion of regulatory evidence into parent/child chunks.

Idempotency: each source's SHA-256 checksum is stored on its chunks. Re-running
ingestion is a no-op when the checksum is unchanged; when a source file changes,
its old chunks are deleted and replaced (a controlled re-ingest). No original
government document is modified.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.regulatory import RegulatoryChunk
from app.services.compliance.result_builder import LEGAL_CUTOFF_DATE
from app.services.compliance.rule_loader import load_compliance_rules
from app.services.regulatory.chunking import (
    split_child_chunks,
    split_parent_sections,
)
from app.services.regulatory.sources import (
    PROJECT_ROOT,
    RegulatorySource,
    discover_sources,
    iter_units,
)

INGESTION_VERSION = "regulatory-ingest-v1"


@dataclass
class SourceResult:
    key: str
    source_document: str
    status: str  # "ingested" | "updated" | "skipped_idempotent" | "error"
    pages_processed: int = 0
    ocr_pages: int = 0
    parent_chunks: int = 0
    child_chunks: int = 0
    validation_status: str = ""
    error: str | None = None


@dataclass
class IngestionReport:
    documents_discovered: int = 0
    documents_ingested: int = 0
    documents_updated: int = 0
    documents_skipped_idempotent: int = 0
    documents_errored: int = 0
    pages_processed: int = 0
    ocr_pages: int = 0
    parent_chunks: int = 0
    child_chunks: int = 0
    duplicate_chunk_ids: int = 0
    rule_data_version: str = ""
    ingestion_version: str = INGESTION_VERSION
    sources: list[SourceResult] = field(default_factory=list)


def _checksum(source: RegulatorySource) -> str:
    return "sha256:" + hashlib.sha256(source.path.read_bytes()).hexdigest()


def _relative_path(source: RegulatorySource) -> str:
    return str(source.path.relative_to(PROJECT_ROOT))


def _pdf_page_stats(source: RegulatorySource) -> tuple[int, int]:
    if source.kind != "pdf_pages":
        return (0, 0)
    import fitz  # type: ignore[import-untyped]

    document = fitz.open(source.path)
    try:
        total = document.page_count
        empty = sum(1 for i in range(total) if not document[i].get_text().strip())
        return (total, empty)
    finally:
        document.close()


def ingest_source(
    db: Session,
    source: RegulatorySource,
    rule_data_version: str,
) -> SourceResult:
    checksum = _checksum(source)
    source_path = _relative_path(source)

    existing = list(
        db.execute(
            select(RegulatoryChunk).where(RegulatoryChunk.source_path == source_path)
        ).scalars()
    )
    if existing and existing[0].document_checksum == checksum:
        return SourceResult(
            key=source.key,
            source_document=source.source_document,
            status="skipped_idempotent",
            parent_chunks=sum(1 for c in existing if c.is_parent),
            child_chunks=sum(1 for c in existing if not c.is_parent),
            validation_status=source.validation_status,
        )

    updated = bool(existing)
    for chunk in existing:
        db.delete(chunk)
    db.flush()

    result = SourceResult(
        key=source.key,
        source_document=source.source_document,
        status="updated" if updated else "ingested",
        validation_status=source.validation_status,
    )
    total_pages, empty_pages = _pdf_page_stats(source)
    result.ocr_pages = empty_pages  # pages that would require OCR

    slug = source.key
    unit_number = 0
    for unit in iter_units(source):
        unit_number += 1
        if unit.page_number is not None:
            result.pages_processed += 1
        unit_key = f"pg{unit.page_number}" if unit.page_number else f"u{unit_number}"
        parents = split_parent_sections(unit.text)
        for parent_index, parent_text in enumerate(parents):
            parent_chunk_id = f"{slug}:{unit_key}:p{parent_index}"
            db.add(
                _build_chunk(
                    source=source,
                    checksum=checksum,
                    source_path=source_path,
                    chunk_id=parent_chunk_id,
                    parent_chunk_id=None,
                    role="parent",
                    is_parent=True,
                    chunk_index=parent_index,
                    page_number=unit.page_number,
                    section=unit.section,
                    pct_codes=unit.pct_codes,
                    text=parent_text,
                    rule_data_version=rule_data_version,
                )
            )
            result.parent_chunks += 1
            for child_index, child_text in enumerate(split_child_chunks(parent_text)):
                db.add(
                    _build_chunk(
                        source=source,
                        checksum=checksum,
                        source_path=source_path,
                        chunk_id=f"{parent_chunk_id}:c{child_index}",
                        parent_chunk_id=parent_chunk_id,
                        role="child",
                        is_parent=False,
                        chunk_index=child_index,
                        page_number=unit.page_number,
                        section=unit.section,
                        pct_codes=unit.pct_codes,
                        text=child_text,
                        rule_data_version=rule_data_version,
                    )
                )
                result.child_chunks += 1

    if total_pages:
        result.pages_processed = total_pages
    return result


def _build_chunk(
    *,
    source: RegulatorySource,
    checksum: str,
    source_path: str,
    chunk_id: str,
    parent_chunk_id: str | None,
    role: str,
    is_parent: bool,
    chunk_index: int,
    page_number: int | None,
    section: str | None,
    pct_codes: list[str],
    text: str,
    rule_data_version: str,
) -> RegulatoryChunk:
    return RegulatoryChunk(
        chunk_id=chunk_id,
        parent_chunk_id=parent_chunk_id,
        role=role,
        is_parent=is_parent,
        chunk_index=chunk_index,
        source_document=source.source_document,
        source_path=source_path,
        source_url=source.source_url,
        document_checksum=checksum,
        issuing_authority=source.issuing_authority,
        document_type=source.document_type,
        sro_number=source.sro_number,
        page_number=page_number,
        section=section,
        pct_codes=pct_codes,
        source_kind=source.source_kind,
        currency_status=source.currency_status,
        issue_date=source.issue_date,
        effective_date=source.effective_date,
        legal_cutoff_date=LEGAL_CUTOFF_DATE,
        validation_status=source.validation_status,
        rule_data_version=rule_data_version,
        ingestion_version=INGESTION_VERSION,
        text=text,
        char_count=len(text),
    )


def ingest_all(db: Session, rule_data_version: str | None = None) -> IngestionReport:
    if rule_data_version is None:
        rule_data_version = load_compliance_rules().rule_data_version
    report = IngestionReport(rule_data_version=rule_data_version)
    sources = discover_sources()
    report.documents_discovered = len(sources)

    for source in sources:
        try:
            result = ingest_source(db, source, rule_data_version)
        except Exception as exc:  # keep other sources ingesting
            db.rollback()
            report.documents_errored += 1
            report.sources.append(
                SourceResult(
                    key=source.key,
                    source_document=source.source_document,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        report.sources.append(result)
        report.pages_processed += result.pages_processed
        report.ocr_pages += result.ocr_pages
        report.parent_chunks += result.parent_chunks
        report.child_chunks += result.child_chunks
        if result.status == "ingested":
            report.documents_ingested += 1
        elif result.status == "updated":
            report.documents_updated += 1
        elif result.status == "skipped_idempotent":
            report.documents_skipped_idempotent += 1

    db.commit()
    return report
