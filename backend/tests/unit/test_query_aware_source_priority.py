"""Query-aware provenance ordering for the existing hybrid retriever.

All candidates still pass the normal hybrid relevance and evidence gates.
These tests only prove the final source-compatibility/currentness preference;
they do not introduce a second retrieval path or call an external model.
"""

from __future__ import annotations

from time import perf_counter

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models.regulatory import RegulatoryChunk
from app.services.regulatory.embeddings import FakeEmbeddingProvider
from app.services.regulatory.reranker import LexicalReranker
from app.services.regulatory.retrieval import search_regulatory_evidence
from tests.unit.test_regulatory_retrieval import add_evidence


def _add(
    db: Session,
    *,
    key: str,
    title: str,
    text: str,
    document_type: str,
    pct_codes: list[str] | None = None,
    validation_status: str = "verified",
    currency_status: str = "current",
) -> None:
    add_evidence(
        db,
        key=key,
        source_document=title,
        parent_text=text,
        pct_codes=pct_codes or [],
        document_type=document_type,
        validation_status=validation_status,
    )
    chunks = db.scalars(
        select(RegulatoryChunk).where(RegulatoryChunk.chunk_id.like(f"{key}:%"))
    )
    for chunk in chunks:
        chunk.currency_status = currency_status
    db.commit()


def _search(db: Session, query: str, *, pct_code: str | None = None):
    return search_regulatory_evidence(
        db,
        query=query,
        pct_code=pct_code,
        top_k=5,
        embedder=FakeEmbeddingProvider(),
        reranker=LexicalReranker(),
    )


def test_exact_tariff_query_prefers_official_tariff(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        _add(
            db,
            key="tariff",
            title="Pakistan Customs Tariff FY 2025-26",
            text="PCT 6203.4200 Men's or boys' trousers of cotton.",
            document_type="tariff_schedule",
            pct_codes=["62034200"],
        )
        _add(
            db,
            key="summary",
            title="PSW/TIPP textile product export requirements (curated)",
            text="Configured product PCT 62034200 cotton trousers tariff description.",
            document_type="product_requirements_structured",
            pct_codes=["62034200"],
        )
        output = _search(
            db,
            "What is the tariff description for PCT 62034200?",
            pct_code="62034200",
        )
    assert output.results[0].chunk.document_type == "tariff_schedule"


def test_customs_act_question_prefers_customs_act(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        _add(
            db,
            key="act",
            title="Customs Act, 1969",
            text="An officer of customs may seize goods liable to confiscation.",
            document_type="customs_act",
        )
        _add(
            db,
            key="summary",
            title="CACE curated seizure summary",
            text="Customs officers have seizure powers and goods may be confiscated.",
            document_type="product_requirements_structured",
        )
        output = _search(db, "What powers do customs officers have regarding seizure?")
    assert output.results[0].chunk.document_type == "customs_act"


def test_customs_rules_question_prefers_current_rules(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        _add(
            db,
            key="rules",
            title="Customs Rules, 2001 (updated 31 August 2025)",
            text="The goods declaration for export shall be filed in the prescribed form.",
            document_type="customs_rules",
        )
        _add(
            db,
            key="summary",
            title="CACE curated goods-declaration summary",
            text="The configured export workflow uses a goods declaration.",
            document_type="product_requirements_structured",
        )
        output = _search(db, "What do the Customs Rules say about goods declarations?")
    assert output.results[0].chunk.document_type == "customs_rules"


def test_epo_schedule_question_prefers_current_policy(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        _add(
            db,
            key="epo",
            title="Export Policy Order, 2022 - SRO 544(I)/2022",
            text="Schedule II serial 9 covers cotton under PCT 5201.0000.",
            document_type="export_policy_order",
            pct_codes=["52010090"],
        )
        _add(
            db,
            key="summary",
            title="PSW/TIPP textile product export requirements (curated)",
            text="Raw cotton is configured from Export Policy Order Schedule II.",
            document_type="product_requirements_structured",
            pct_codes=["52010090"],
        )
        output = _search(
            db,
            "Which Export Policy Order schedule covers raw cotton?",
            pct_code="52010090",
        )
    assert output.results[0].chunk.document_type == "export_policy_order"


def test_psw_workflow_prefers_matching_current_manual(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        text = "An electronic Certificate of Origin application is processed and submitted."
        _add(
            db,
            key="current",
            title="PSW User Manual - TDAP Electronic Certificate of Origin",
            text=text,
            document_type="psw_user_manual",
        )
        _add(
            db,
            key="history",
            title="TDAP Step-by-Step Guide for New Exporters (2020)",
            text=text,
            document_type="tdap_exporter_guide",
            currency_status="historical_reference",
        )
        output = _search(db, "How is an electronic Certificate of Origin processed?")
    assert "PSW User Manual" in output.results[0].chunk.source_document


def test_curated_summary_remains_searchable_and_supplemental(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        _add(
            db,
            key="official",
            title="Pakistan Customs Tariff FY 2025-26",
            text="PCT 6105.1000 Men's shirts knitted or crocheted, of cotton.",
            document_type="tariff_schedule",
            pct_codes=["61051000"],
        )
        _add(
            db,
            key="summary",
            title="PSW/TIPP textile product export requirements (curated)",
            text="PCT 61051000 is configured for men's knitted cotton shirts.",
            document_type="product_requirements_structured",
            pct_codes=["61051000"],
        )
        output = _search(
            db,
            "Which tariff entry covers PCT 61051000?",
            pct_code="61051000",
        )
    assert output.results[0].chunk.document_type == "tariff_schedule"
    assert any(
        item.chunk.document_type == "product_requirements_structured"
        for item in output.results[1:]
    )


def test_irrelevant_official_passage_does_not_win(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        _add(
            db,
            key="official",
            title="Pakistan Customs Tariff FY 2025-26",
            text="Administrative preface and publication contact details.",
            document_type="tariff_schedule",
            pct_codes=["62034200"],
        )
        _add(
            db,
            key="summary",
            title="PSW/TIPP textile product export requirements (curated)",
            text="PCT 62034200 tariff description is men's woven cotton trousers.",
            document_type="product_requirements_structured",
            pct_codes=["62034200"],
        )
        output = _search(
            db,
            "What is the tariff description for PCT 62034200?",
            pct_code="62034200",
        )
    assert output.results[0].chunk.document_type == "product_requirements_structured"


def test_current_source_wins_when_relevance_is_comparable(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        text = "A Single Declaration is submitted electronically for export."
        _add(
            db,
            key="current",
            title="PSW User Manual - Single Declaration (Exports)",
            text=text,
            document_type="psw_user_manual",
        )
        _add(
            db,
            key="history",
            title="Historical PSW Single Declaration Manual",
            text=text,
            document_type="psw_user_manual",
            currency_status="historical_reference",
        )
        output = _search(db, "How is a Single Declaration submitted for export?")
    assert output.results[0].chunk.currency_status == "current"


def test_historical_source_is_available_when_requested(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        text = "A Single Declaration is submitted electronically for export."
        _add(
            db,
            key="current",
            title="PSW User Manual - Single Declaration (Exports)",
            text=text,
            document_type="psw_user_manual",
        )
        _add(
            db,
            key="history",
            title="Historical PSW Single Declaration Manual",
            text=text,
            document_type="psw_user_manual",
            currency_status="historical_reference",
        )
        output = _search(
            db,
            "What did the historical Single Declaration manual say about export?",
        )
    assert output.results[0].chunk.currency_status == "historical_reference"


def test_blocked_source_is_never_accepted(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        _add(
            db,
            key="blocked",
            title="Blocked Export Policy amendment",
            text="Appendix J raw cotton PCT 5201.0090 unresolved OCR value.",
            document_type="export_policy_amendment",
            pct_codes=["52010090"],
            validation_status="blocked",
        )
        output = _search(
            db,
            "What does Appendix-J say about raw cotton?",
            pct_code="52010090",
        )
    assert output.status == "evidence_not_found"


def test_source_priority_adds_no_material_small_corpus_latency(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        for index in range(20):
            _add(
                db,
                key=f"manual_{index}",
                title="PSW User Manual - Single Declaration (Exports)",
                text=f"Single Declaration export submission procedure step {index}.",
                document_type="psw_user_manual",
            )
        started = perf_counter()
        output = _search(db, "How is a Single Declaration submitted for export?")
        elapsed = perf_counter() - started
    assert output.status == "ok"
    assert elapsed < 0.5
