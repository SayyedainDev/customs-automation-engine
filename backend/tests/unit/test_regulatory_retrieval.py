"""Phase 3B RAG tests: embeddings, vector store, hybrid retrieval, API.

All model-dependent behaviour uses injected fake embedders/rerankers. No large
model is downloaded.
"""

import asyncio

import httpx
import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base, get_db_session
from app.main import app
from app.models.regulatory import RegulatoryChunk, RegulatoryChunkVector
from app.services.regulatory import embeddings as embeddings_module
from app.services.regulatory import reranker as reranker_module
from app.services.regulatory.embeddings import (
    FakeEmbeddingProvider,
    HashingEmbeddingProvider,
    get_embedding_provider,
    reset_embedding_provider,
)
from app.services.regulatory.query_builder import (
    ComplianceQueryInputs,
    build_compliance_query,
)
from app.services.regulatory.reranker import (
    FakeReranker,
    LexicalReranker,
    get_reranker,
    reset_reranker,
)
from app.services.regulatory.retrieval import search_regulatory_evidence
from app.services.regulatory.vector_store import (
    build_vector_index,
    get_vector_index_meta,
)


@pytest.fixture(autouse=True)
def _reset_providers():
    reset_embedding_provider()
    reset_reranker()
    yield
    reset_embedding_provider()
    reset_reranker()


def add_evidence(
    db: Session,
    *,
    key: str,
    source_document: str,
    parent_text: str,
    children: list[str] | None = None,
    pct_codes: list[str],
    validation_status: str = "verified",
    sro_number: str | None = None,
    page_number: int | None = None,
    section: str | None = None,
    source_url: str | None = "https://example.gov.pk/doc",
    document_type: str = "test_evidence",
    issuing_authority: str = "Government of Pakistan",
) -> None:
    parent_id = f"{key}:p0"
    common = dict(
        source_document=source_document,
        source_path=f"regulatory_data/test/{key}.txt",
        source_url=source_url,
        document_checksum="sha256:testfixture",
        issuing_authority=issuing_authority,
        document_type=document_type,
        sro_number=sro_number,
        page_number=page_number,
        section=section,
        pct_codes=pct_codes,
        validation_status=validation_status,
        rule_data_version="sha256:testrules",
        ingestion_version="test-ingest",
    )
    db.add(RegulatoryChunk(chunk_id=parent_id, parent_chunk_id=None, role="parent", is_parent=True, chunk_index=0, text=parent_text, char_count=len(parent_text), **common))
    for index, child_text in enumerate(children or [parent_text]):
        db.add(RegulatoryChunk(chunk_id=f"{parent_id}:c{index}", parent_chunk_id=parent_id, role="child", is_parent=False, chunk_index=index, text=child_text, char_count=len(child_text), **common))
    db.commit()


def build_corpus(db: Session) -> None:
    add_evidence(
        db, key="sro_2486",
        source_document="SRO 2486(I)/2025 — amendment to Export Policy Order, 2022",
        parent_text="S.R.O. 2486(I)/2025. Security deposit of 1% of the contract value with the State Bank of Pakistan and a confirmation letter before customs. An irrevocable letter of credit shall be opened and shipment completed within one hundred and eighty days.",
        children=[
            "S.R.O. 2486(I)/2025. Security deposit of 1% of the contract value with the State Bank of Pakistan before shipping cotton.",
            "An irrevocable letter of credit shall be opened and shipment completed within one hundred and eighty days.",
        ],
        pct_codes=["52010090"], validation_status="verified", sro_number="2486(I)/2025", page_number=1, section="SRO 2486(I)/2025", document_type="export_policy_amendment",
    )
    add_evidence(
        db, key="coo",
        source_document="PSW/TIPP textile product export requirements (curated)",
        parent_text="Conditional certificate of origin for textile exports. China under CPFTA: certificate_required=true. Trade Development Authority of Pakistan issues the certificate of origin.",
        pct_codes=["52051100", "52094200", "61091000", "63023110"], validation_status="partially_verified", section="conditional_certificate_of_origin", document_type="product_requirements_structured",
    )
    add_evidence(
        db, key="form_e",
        source_document="PSW/TIPP textile product export requirements (curated)",
        parent_text="Common export clearance for all textile products requires a commercial invoice, a packing list and a Form-E for export.",
        pct_codes=["52010090", "52051100", "52094200", "61091000", "63023110"], validation_status="partially_verified", section="common_export_clearance", document_type="product_requirements_structured",
    )
    add_evidence(
        db, key="unverified",
        source_document="Unverified working note",
        parent_text="Cotton t-shirt zxqmarker draft requirement not verified.",
        pct_codes=["61091000"], validation_status="unverified",
    )


# 1. Dense embeddings generated for regulatory chunks.
def test_dense_embeddings_generated_for_chunks(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        report = build_vector_index(db, FakeEmbeddingProvider(dimension=16))
        count = db.execute(select(func.count()).select_from(RegulatoryChunkVector)).scalar()
    assert report.chunks_embedded == count
    assert count > 0
    assert report.embedding_dim == 16


# 2. Embedding batches.
def test_embedding_batches() -> None:
    provider = HashingEmbeddingProvider(dimension=32)
    vectors = provider.embed([f"document number {i}" for i in range(70)], batch_size=16)
    assert vectors.shape == (70, 32)
    # L2 normalized rows.
    norms = (vectors ** 2).sum(axis=1) ** 0.5
    assert all(abs(n - 1.0) < 1e-5 or n == 0 for n in norms)


# 3. Vector-index idempotency.
def test_vector_index_idempotency(isolated_database: Engine) -> None:
    embedder = FakeEmbeddingProvider(dimension=16)
    with Session(isolated_database) as db:
        build_corpus(db)
        build_vector_index(db, embedder)
        first = db.execute(select(func.count()).select_from(RegulatoryChunkVector)).scalar()
        report = build_vector_index(db, embedder)
        second = db.execute(select(func.count()).select_from(RegulatoryChunkVector)).scalar()
    assert first == second
    assert report.chunks_embedded == 0
    assert report.chunks_skipped == first


# 4. Vector update after document checksum change.
def test_vector_update_after_checksum_change(isolated_database: Engine) -> None:
    embedder = FakeEmbeddingProvider(dimension=16)
    with Session(isolated_database) as db:
        build_corpus(db)
        build_vector_index(db, embedder)
        for chunk in db.execute(select(RegulatoryChunk).where(RegulatoryChunk.is_parent.is_(False))).scalars():
            chunk.document_checksum = "sha256:changed"
        db.commit()
        report = build_vector_index(db, embedder)
    assert report.chunks_updated > 0
    assert report.chunks_embedded == 0


# 5. Stale-vector deletion.
def test_stale_vector_deletion(isolated_database: Engine) -> None:
    embedder = FakeEmbeddingProvider(dimension=16)
    with Session(isolated_database) as db:
        build_corpus(db)
        build_vector_index(db, embedder)
        before = db.execute(select(func.count()).select_from(RegulatoryChunkVector)).scalar()
        # Remove one document's chunks.
        for chunk in db.execute(select(RegulatoryChunk).where(RegulatoryChunk.chunk_id.like("unverified:%"))).scalars():
            db.delete(chunk)
        db.commit()
        report = build_vector_index(db, embedder)
        after = db.execute(select(func.count()).select_from(RegulatoryChunkVector)).scalar()
    assert report.stale_removed >= 1
    assert before is not None and after is not None
    assert after < before


# 6. Dense semantic retrieval drives ranking.
def test_dense_semantic_retrieval(isolated_database: Engine) -> None:
    # Two docs with identical query keywords (equal BM25); dense decides order.
    # No pct_code, so the query is not augmented and the mapping keys match.
    with Session(isolated_database) as db:
        add_evidence(db, key="target", source_document="Target doc", parent_text="cotton deposit rule alpha", pct_codes=["52010090"])
        add_evidence(db, key="distractor", source_document="Distractor doc", parent_text="cotton deposit rule beta", pct_codes=["52010090"])
        mapping = {
            "cotton deposit rule query": [1.0, 0.0, 0.0],
            "cotton deposit rule alpha": [1.0, 0.0, 0.0],   # aligned with query
            "cotton deposit rule beta": [0.0, 1.0, 0.0],    # orthogonal
        }
        embedder = FakeEmbeddingProvider(dimension=3, mapping=mapping)
        out = search_regulatory_evidence(
            db, query="cotton deposit rule query",
            embedder=embedder, reranker=FakeReranker(),
        )
    assert out.status == "ok"
    assert out.results[0].chunk.source_document == "Target doc"
    assert out.results[0].dense_score >= out.results[-1].dense_score


# 7. Exact PCT BM25 retrieval.
def test_exact_pct_bm25_retrieval(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        out = search_regulatory_evidence(db, query="PCT 52010090", pct_code="52010090", embedder=FakeEmbeddingProvider(), reranker=LexicalReranker())
    assert out.status == "ok"
    pct_codes = out.results[0].chunk.pct_codes
    assert pct_codes is not None
    assert "52010090" in pct_codes


# 8. Exact SRO BM25 retrieval.
def test_exact_sro_bm25_retrieval(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        out = search_regulatory_evidence(db, query="SRO 2486(I)/2025", embedder=FakeEmbeddingProvider(), reranker=LexicalReranker())
    assert out.status == "ok"
    assert out.results[0].chunk.sro_number == "2486(I)/2025"


# 9. RRF combines BM25 and dense.
def test_rrf_combination(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        out = search_regulatory_evidence(db, query="raw cotton security deposit State Bank", pct_code="52010090", embedder=FakeEmbeddingProvider(), reranker=LexicalReranker())
    assert out.status == "ok"
    for result in out.results:
        assert result.rrf_score > 0
        assert result.rrf_rank >= 1
        assert result.bm25_rank >= 1 and result.dense_rank >= 1


# 10. Real cross-encoder reranking through an injectable interface.
def test_cross_encoder_reranking_injectable(isolated_database: Engine) -> None:
    # Two docs both pass the lexical relevance gate; the injected reranker
    # controls which one is ranked first.
    with Session(isolated_database) as db:
        add_evidence(db, key="a", source_document="Doc A", parent_text="cotton export certificate of origin requirement alpha", pct_codes=["61091000"])
        add_evidence(db, key="b", source_document="Doc B", parent_text="cotton export certificate of origin requirement beta", pct_codes=["61091000"])
        reranker = FakeReranker(
            mapping={"cotton export certificate of origin requirement beta": 99.0}
        )
        out = search_regulatory_evidence(
            db, query="cotton export certificate of origin requirement",
            embedder=FakeEmbeddingProvider(), reranker=reranker,
        )
    assert out.status == "ok"
    assert out.results[0].chunk.source_document == "Doc B"
    assert out.results[0].cross_encoder_score == 99.0


# 11. Parent-child expansion.
def test_parent_child_expansion(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        out = search_regulatory_evidence(db, query="irrevocable letter of credit cotton", pct_code="52010090", embedder=FakeEmbeddingProvider(), reranker=LexicalReranker())
    result = out.results[0]
    assert result.parent.is_parent is True
    assert result.chunk.parent_chunk_id == result.parent.chunk_id
    assert len(result.parent.text) >= len(result.chunk.text)


# 12. Metadata filters.
def test_metadata_filters(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        by_sro = search_regulatory_evidence(db, query="deposit", sro_number="2486(I)/2025", embedder=FakeEmbeddingProvider(), reranker=LexicalReranker())
        by_type = search_regulatory_evidence(db, query="certificate of origin China", document_type="product_requirements_structured", pct_code="61091000", embedder=FakeEmbeddingProvider(), reranker=LexicalReranker())
    assert by_sro.status == "ok"
    assert all(r.chunk.sro_number == "2486(I)/2025" for r in by_sro.results)
    assert by_type.status == "ok"
    assert all(r.chunk.document_type == "product_requirements_structured" for r in by_type.results)


# 13. Verified-only retrieval.
def test_verified_only_retrieval(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        verified = search_regulatory_evidence(db, query="zxqmarker", verified_only=True, embedder=FakeEmbeddingProvider(), reranker=LexicalReranker())
        allowed = search_regulatory_evidence(db, query="zxqmarker", verified_only=False, embedder=FakeEmbeddingProvider(), reranker=LexicalReranker())
    assert verified.status == "evidence_not_found"
    assert allowed.status == "ok"


# 14. No verified evidence.
def test_no_verified_evidence(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        out = search_regulatory_evidence(db, query="tomorrow weather forecast seaport", embedder=FakeEmbeddingProvider(), reranker=LexicalReranker())
    assert out.status == "evidence_not_found"


# 16. Deterministic compliance-query construction.
def test_deterministic_query_construction() -> None:
    query = build_compliance_query(
        ComplianceQueryInputs(
            check_id="xr_52010090_sbp_deposit_proof",
            check_name="Proof of 1% SBP security deposit for raw cotton",
            pct_code="52010090",
            required_document="sbp_deposit_proof",
            sro_number="2486(I)/2025",
        )
    )
    assert "PCT 52010090" in query
    assert "raw cotton" in query
    assert "SRO 2486(I)/2025" in query
    # Deterministic: same inputs -> same query.
    again = build_compliance_query(
        ComplianceQueryInputs(check_id="xr_52010090_sbp_deposit_proof", check_name="Proof of 1% SBP security deposit for raw cotton", pct_code="52010090", required_document="sbp_deposit_proof", sro_number="2486(I)/2025")
    )
    assert query == again


# 21. Model-unavailable degraded mode.
def test_model_unavailable_degraded_mode(
    isolated_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("model unavailable in this test")

    monkeypatch.setattr(
        embeddings_module,
        "SentenceTransformerEmbeddingProvider",
        unavailable,
    )
    monkeypatch.setattr(reranker_module, "CrossEncoderReranker", unavailable)

    assert get_embedding_provider().degraded is True
    initialization_error = next(
        record
        for record in caplog.records
        if record.name == "app.services.regulatory.embeddings"
    )
    settings = get_settings()
    assert initialization_error.exc_info is not None
    assert repr(settings.regulatory_embedding_model) in initialization_error.getMessage()
    assert repr(settings.regulatory_embedding_device) in initialization_error.getMessage()
    assert get_reranker().degraded is True
    with Session(isolated_database) as db:
        build_corpus(db)
        out = search_regulatory_evidence(db, query="raw cotton deposit", pct_code="52010090")
    assert out.degraded_mode is True
    assert "degraded" in out.embedding_model
    assert "degraded" in out.reranker_model


def test_degraded_query_reembeds_incompatible_persisted_vectors_in_memory(
    isolated_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_model = "sentence-transformers/all-MiniLM-L6-v2"
    with Session(isolated_database) as db:
        build_corpus(db)
        build_vector_index(
            db,
            FakeEmbeddingProvider(dimension=384, model_name=real_model),
        )

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("model unavailable in this test")

    monkeypatch.setattr(
        embeddings_module,
        "SentenceTransformerEmbeddingProvider",
        unavailable,
    )
    monkeypatch.setattr(reranker_module, "CrossEncoderReranker", unavailable)

    with Session(isolated_database) as db:
        out = search_regulatory_evidence(
            db,
            query="raw cotton security deposit",
            pct_code="52010090",
        )
        stored = list(db.execute(select(RegulatoryChunkVector)).scalars())

    assert out.status == "ok"
    assert out.degraded_mode is True
    assert out.results
    assert all(row.embedding_model == real_model for row in stored)
    assert all(row.embedding_dim == 384 for row in stored)
    assert all(len(row.embedding) == 384 for row in stored)


# 22. Persistent index reload after application restart.
def test_persistent_index_reload_after_restart(tmp_path) -> None:
    db_path = tmp_path / "reg_index.sqlite3"
    url = f"sqlite+pysqlite:///{db_path}"
    embedder = FakeEmbeddingProvider(dimension=16)

    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        build_corpus(db)
        build_vector_index(db, embedder)
        count_before = db.execute(select(func.count()).select_from(RegulatoryChunkVector)).scalar()
    engine.dispose()  # simulate application shutdown

    engine2 = create_engine(url)  # fresh engine = restart
    with Session(engine2) as db:
        count_after = db.execute(select(func.count()).select_from(RegulatoryChunkVector)).scalar()
        meta = get_vector_index_meta(db)
    engine2.dispose()
    assert count_before == count_after
    assert count_after is not None and count_after > 0
    assert meta["vector_count"] == count_after


# 23. HTTP search endpoint.
def test_http_search_endpoint(
    isolated_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("model unavailable in this test")

    monkeypatch.setattr(
        embeddings_module,
        "SentenceTransformerEmbeddingProvider",
        unavailable,
    )
    monkeypatch.setattr(reranker_module, "CrossEncoderReranker", unavailable)

    with Session(isolated_database) as db:
        build_corpus(db)

    async def override_db():
        with Session(isolated_database) as db:
            yield db

    app.dependency_overrides[get_db_session] = override_db

    async def post():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                "/api/v1/regulatory-evidence/search",
                json={"query": "raw cotton SBP deposit", "pct_code": "52010090", "top_k": 3, "verified_only": True},
            )

    response = asyncio.run(post())
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["degraded_mode"] is True
    assert "retrieval_mode" in body and body["results"]
    assert "bm25_score" in body["results"][0]


# 25. Existing compliance engine remains unchanged (sanity).
def test_existing_compliance_engine_unchanged() -> None:
    from datetime import date
    from decimal import Decimal

    from app.schemas.compliance import ComplianceCheckStatus, ShipmentComplianceInput
    from app.services.compliance.rule_engine import DeterministicComplianceRuleEngine

    shipment = ShipmentComplianceInput(
        product_name="Raw cotton", pct_code="52010090", quantity=Decimal("1000"),
        unit_price=Decimal("2"), invoice_line_total=Decimal("2000"), invoice_total=Decimal("2000"),
        net_weight=Decimal("1000"), gross_weight=Decimal("1025"), destination_country="UAE",
        shipment_date=date(2026, 6, 1), letter_of_credit_date=date(2026, 1, 1),
        uploaded_document_types=["commercial_invoice", "packing_list", "form_e"],
    )
    result = DeterministicComplianceRuleEngine().check(shipment)
    # The deterministic engine still decides; RAG never overrides it.
    assert result.overall_status in {ComplianceCheckStatus.FAILED, ComplianceCheckStatus.MANUAL_REVIEW}
