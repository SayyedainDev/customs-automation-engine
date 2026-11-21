"""Scenario evidence for hybrid regulatory evidence search and provenance classification."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.regulatory import RegulatoryChunk
from app.services.regulatory.embeddings import FakeEmbeddingProvider
from app.services.regulatory.reranker import LexicalReranker
from app.services.regulatory.retrieval import search_regulatory_evidence
from app.services.regulatory.source_kinds import (
    CURATED_RULE_SUMMARY,
    OFFICIAL_REGULATION,
    classify_source_kind,
)
from cace_testing.harnesses.database import open_isolated_session


def _seed_chunk(
    db: Session,
    *,
    key: str,
    source_document: str,
    parent_text: str,
    child_text: str,
    pct_codes: list[str],
    validation_status: str,
    sro_number: str | None,
    document_type: str,
) -> None:
    parent_id = f"{key}:p0"
    common = {
        "source_document": source_document,
        "source_path": f"regulatory_data/{key}.txt",
        "source_url": "https://www.fbr.gov.pk/",
        "document_checksum": f"sha256:{key}",
        "issuing_authority": "Ministry of Commerce",
        "document_type": document_type,
        "sro_number": sro_number,
        "page_number": 1,
        "section": "Clause 4",
        "pct_codes": pct_codes,
        "validation_status": validation_status,
        "rule_data_version": "sha256:testrules",
        "ingestion_version": "test-v1",
    }
    db.add(
        RegulatoryChunk(
            chunk_id=parent_id,
            parent_chunk_id=None,
            role="parent",
            is_parent=True,
            chunk_index=0,
            text=parent_text,
            char_count=len(parent_text),
            **common,
        )
    )
    db.add(
        RegulatoryChunk(
            chunk_id=f"{parent_id}:c0",
            parent_chunk_id=parent_id,
            role="child",
            is_parent=False,
            chunk_index=0,
            text=child_text,
            char_count=len(child_text),
            **common,
        )
    )
    db.commit()


def test_regulatory_search_returns_verified_parent_and_rejects_unrelated_queries() -> None:
    with open_isolated_session() as db:
        _seed_chunk(
            db,
            key="sro_2486",
            source_document="S.R.O. 2486(I)/2025",
            parent_text=(
                "Raw cotton exports under PCT 5201.0090 require a 1% bank deposit "
                "with State Bank of Pakistan and phytosanitary certification."
            ),
            child_text=(
                "Raw cotton PCT 5201.0090 requires a 1% SBP security deposit."
            ),
            pct_codes=["52010090"],
            validation_status="verified",
            sro_number="2486(I)/2025",
            document_type="sro",
        )
        _seed_chunk(
            db,
            key="draft_unverified",
            source_document="Draft Circular",
            parent_text="Draft unverified raw cotton note requiring 9% levy.",
            child_text="Draft unverified raw cotton note requiring 9% levy.",
            pct_codes=["52010090"],
            validation_status="unverified",
            sro_number=None,
            document_type="sro",
        )

        embedder = FakeEmbeddingProvider()
        reranker = LexicalReranker()

        found = search_regulatory_evidence(
            db,
            query="raw cotton security deposit 52010090",
            pct_code="5201.0090",
            verified_only=True,
            embedder=embedder,
            reranker=reranker,
        )
        assert found.status == "ok"
        assert found.degraded_mode is True
        assert len(found.results) == 1
        assert found.results[0].chunk.chunk_id == "sro_2486:p0:c0"
        assert "phytosanitary" in found.results[0].parent.text

        unrelated = search_regulatory_evidence(
            db,
            query="orbital launch propellant taxation",
            verified_only=True,
            embedder=embedder,
            reranker=reranker,
        )
        assert unrelated.status == "evidence_not_found"
        assert unrelated.results == []


def test_source_kind_classification_separates_curated_from_official() -> None:
    curated = classify_source_kind(
        source_document="Textile Product Requirements (curated)",
        document_type="sro",
        issuing_authority="Government of Pakistan",
    )
    official = classify_source_kind(
        source_document="S.R.O. 2486(I)/2025",
        document_type="sro",
        issuing_authority="Ministry of Commerce",
    )
    assert curated == CURATED_RULE_SUMMARY
    assert official == OFFICIAL_REGULATION
