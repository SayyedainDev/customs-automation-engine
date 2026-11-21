from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.schemas.ocr import OcrPageResult, OcrValidationStatus
from app.services import structured_extraction_service
from app.services.extraction.ocr_extractor import (
    OcrConfigurationError,
    OcrExtractionError,
)
from app.services.extraction.pdf_extractor import extract_text_from_pdf
from scripts import benchmark_extraction as bench


def _scanned_invoice_doc(scenario: str = "clean_cotton_tshirts") -> bench.BenchmarkDocument:
    docs = bench.load_benchmark_documents(
        variant="scanned",
        scenario_filter=scenario,
    )
    return next(d for d in docs if d.document_type == "commercial_invoice")


def _paired_digital_text(doc: bench.BenchmarkDocument) -> str:
    digital_path = doc.pdf_path.with_name(
        doc.pdf_path.name.replace("_scanned.pdf", "_text.pdf")
    )
    pdf_result = extract_text_from_pdf(digital_path)
    return "\n\n".join(page.text for page in pdf_result.pages)


def _make_ocr_result(
    *,
    document_id: UUID,
    page_number: int,
    original_embedded_text: str,
    ocr_text: str,
    confidence: str = "0.940",
    status: OcrValidationStatus = OcrValidationStatus.VERIFIED,
) -> OcrPageResult:
    return OcrPageResult(
        document_id=document_id,
        page_number=page_number,
        original_embedded_text=original_embedded_text,
        ocr_text=ocr_text,
        ocr_confidence=Decimal(confidence),
        validation_status=status,
        validation_note="Simulated Tesseract OCR output.",
    )


def test_load_benchmark_documents_scanned_and_both() -> None:
    scanned_docs = bench.load_benchmark_documents(variant="scanned")
    assert len(scanned_docs) == 32
    assert all(doc.variant == "scanned" for doc in scanned_docs)
    assert all(doc.pdf_path.is_file() for doc in scanned_docs)
    assert all(doc.pdf_path.name.endswith("_scanned.pdf") for doc in scanned_docs)

    both_docs = bench.load_benchmark_documents(variant="both")
    assert len(both_docs) == 64
    assert sum(1 for doc in both_docs if doc.variant == "text") == 32
    assert sum(1 for doc in both_docs if doc.variant == "scanned") == 32

    limited_both = bench.load_benchmark_documents(variant="both", limit=4)
    assert len(limited_both) == 4
    assert sum(1 for doc in limited_both if doc.variant == "text") == 2
    assert sum(1 for doc in limited_both if doc.variant == "scanned") == 2
    assert {doc.scenario_id for doc in limited_both} == {limited_both[0].scenario_id}

    with pytest.raises(ValueError, match="Unsupported --variant"):
        bench.load_benchmark_documents(variant="invalid")


def test_load_pdf_bundle_scanned_ocr_and_disk_cache(tmp_path: Path) -> None:
    doc = _scanned_invoice_doc()
    real_text = _paired_digital_text(doc)
    calls: list[int] = []

    def fake_ocr(
        *,
        pdf_path: Path,
        document_id: UUID,
        page_number: int,
        original_embedded_text: str,
    ) -> OcrPageResult:
        calls.append(page_number)
        assert pdf_path == doc.pdf_path
        return _make_ocr_result(
            document_id=document_id,
            page_number=page_number,
            original_embedded_text=original_embedded_text,
            ocr_text=real_text,
        )

    first = bench._load_pdf_bundle(
        doc,
        cache_dir=tmp_path,
        use_cache=True,
        ocr_fn=fake_ocr,
    )
    assert len(calls) >= 1
    initial_calls = len(calls)
    assert not first.ocr_cache_hit
    assert first.ocr_pages_count == initial_calls
    assert len(first.bundle.useful_pages) == initial_calls
    assert all(page.words == [] for page in first.bundle.pages)
    assert all(page.extraction_method == "tesseract_ocr" for page in first.bundle.pages)
    assert all(page.ocr_confidence == Decimal("0.940") for page in first.bundle.pages)
    assert all(
        page.ocr_validation_status == OcrValidationStatus.VERIFIED
        for page in first.bundle.pages
    )
    assert all(
        review.requires_ocr and review.ocr_attempted
        for review in first.bundle.reviews
    )

    cache_file = tmp_path / f"ocr__{doc.document_id}.json"
    assert cache_file.is_file()

    second = bench._load_pdf_bundle(
        doc,
        cache_dir=tmp_path,
        use_cache=True,
        ocr_fn=fake_ocr,
    )
    assert second.ocr_cache_hit is True
    assert len(calls) == initial_calls
    assert [p.text for p in second.bundle.pages] == [p.text for p in first.bundle.pages]

    third = bench._load_pdf_bundle(
        doc,
        cache_dir=tmp_path,
        use_cache=False,
        ocr_fn=fake_ocr,
    )
    assert third.ocr_cache_hit is False
    assert len(calls) == initial_calls * 2


def test_load_pdf_bundle_scanned_short_text_and_errors(tmp_path: Path) -> None:
    doc = _scanned_invoice_doc()

    def short_ocr(
        *,
        pdf_path: Path,
        document_id: UUID,
        page_number: int,
        original_embedded_text: str,
    ) -> OcrPageResult:
        return _make_ocr_result(
            document_id=document_id,
            page_number=page_number,
            original_embedded_text=original_embedded_text,
            ocr_text="abc",
        )

    short_loaded = bench._load_pdf_bundle(doc, cache_dir=None, ocr_fn=short_ocr)
    assert short_loaded.bundle.useful_pages == []
    assert all(
        page.ocr_validation_status == OcrValidationStatus.MANUAL_REVIEW
        for page in short_loaded.bundle.pages
    )
    assert all(
        review.ocr_validation_status == OcrValidationStatus.MANUAL_REVIEW
        for review in short_loaded.bundle.reviews
    )

    def failing_ocr(**kwargs: Any) -> OcrPageResult:
        raise OcrExtractionError("tesseract exited with status 1")

    failed_loaded = bench._load_pdf_bundle(
        doc,
        cache_dir=tmp_path,
        use_cache=True,
        ocr_fn=failing_ocr,
    )
    assert failed_loaded.bundle.useful_pages == []
    assert all(
        review.ocr_validation_status == OcrValidationStatus.FAILED
        for review in failed_loaded.bundle.reviews
    )
    assert not (tmp_path / f"ocr__{doc.document_id}.json").exists()

    def missing_binary_ocr(**kwargs: Any) -> OcrPageResult:
        raise OcrConfigurationError("OCR executable 'tesseract' is not installed or not on PATH.")

    with pytest.raises(OcrConfigurationError, match="tesseract"):
        bench._load_pdf_bundle(doc, cache_dir=tmp_path, ocr_fn=missing_binary_ocr)


def test_main_scanned_missing_tesseract_fails_fast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def missing_tesseract(**kwargs: Any) -> OcrPageResult:
        raise OcrConfigurationError("OCR executable 'tesseract' is not installed or not on PATH.")

    monkeypatch.setattr(bench, "ocr_pdf_page", missing_tesseract)

    exit_code = bench.main(
        [
            "--variant",
            "scanned",
            "--limit",
            "1",
            "--dry-run",
            "--cache-dir",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "tesseract" in captured.err.lower()
    assert "sudo apt install tesseract-ocr" in captured.err
    assert "CUSTOMS_OCR_EXECUTABLE" in captured.err


def test_approaches_a_b_c_on_scanned_bundle_and_ocr_gates() -> None:
    doc = _scanned_invoice_doc()
    real_text = _paired_digital_text(doc)

    verified_loaded = bench._load_pdf_bundle(
        doc,
        ocr_fn=lambda **kw: _make_ocr_result(
            document_id=kw["document_id"],
            page_number=kw["page_number"],
            original_embedded_text=kw["original_embedded_text"],
            ocr_text=real_text,
            confidence="0.950",
            status=OcrValidationStatus.VERIFIED,
        ),
    )
    assert all(p.words == [] for p in verified_loaded.bundle.pages)

    res_b = bench.run_approach_b(doc, loaded_bundle=verified_loaded)
    res_c = bench.run_approach_c(
        doc,
        loaded_bundle=verified_loaded,
        client_factory=bench._make_offline_stub_client_factory(doc),
    )
    res_a = bench.run_approach_a(
        doc,
        loaded_bundle=verified_loaded,
        client_factory=bench._make_offline_stub_client_factory(doc),
    )

    assert res_b.variant == "scanned"
    assert res_b.llm_calls == 0
    assert res_b.total_tokens == 0
    assert res_b.correct_fields > 0

    assert res_c.variant == "scanned"
    assert res_c.provider_failed is False
    assert res_a.variant == "scanned"
    assert res_a.llm_calls == 1
    assert res_a.total_tokens > 0
    assert res_a.provider_failed is False

    # Low-confidence OCR: CACE provenance gate must abstain to manual_review (0 silent errors).
    low_conf_loaded = bench._load_pdf_bundle(
        doc,
        ocr_fn=lambda **kw: _make_ocr_result(
            document_id=kw["document_id"],
            page_number=kw["page_number"],
            original_embedded_text=kw["original_embedded_text"],
            ocr_text=real_text,
            confidence="0.400",
            status=OcrValidationStatus.MANUAL_REVIEW,
        ),
    )
    low_b = bench.run_approach_b(doc, loaded_bundle=low_conf_loaded)
    assert low_b.correct_fields == 0
    assert low_b.silent_error_fields == 0
    assert all(
        score.status in ("manual_review", "missing") for score in low_b.field_scores
    )
    assert any(score.status == "manual_review" for score in low_b.field_scores)

    # Unusable / empty OCR: A, B, and C must make 0 LLM calls.
    empty_loaded = bench._load_pdf_bundle(
        doc,
        ocr_fn=lambda **kw: _make_ocr_result(
            document_id=kw["document_id"],
            page_number=kw["page_number"],
            original_embedded_text=kw["original_embedded_text"],
            ocr_text="",
        ),
    )
    assert empty_loaded.bundle.useful_pages == []

    class _FailIfCalledClient:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError("Groq should not be called when OCR yields no useful pages")

    for runner in (bench.run_approach_a, bench.run_approach_c):
        res = runner(
            doc,
            loaded_bundle=empty_loaded,
            client_factory=lambda: _FailIfCalledClient(),
        )
        assert res.llm_calls == 0
        assert res.total_tokens == 0
        assert res.correct_fields == 0
        assert res.silent_error_fields == 0
        assert all(
            score.status in ("manual_review", "missing") for score in res.field_scores
        )
    res_empty_b = bench.run_approach_b(doc, loaded_bundle=empty_loaded)
    assert res_empty_b.correct_fields == 0
    assert res_empty_b.silent_error_fields == 0
    assert all(
        score.status in ("manual_review", "missing")
        for score in res_empty_b.field_scores
    )


def test_live_call_pacer_delays_only_consecutive_uncached_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = bench.load_benchmark_documents(variant="text", limit=2)
    doc1, doc2 = docs[0], docs[1]
    sleeps: list[float] = []
    pacer = bench._LiveCallPacer(delay_seconds=12.5, sleep_fn=sleeps.append)

    monkeypatch.setattr(
        structured_extraction_service,
        "_get_groq_client",
        lambda: bench._make_offline_stub_client_factory(doc1)(),
    )
    r1 = bench.run_approach_a(doc1, cache_dir=tmp_path, use_cache=True, pacer=pacer)
    assert r1.llm_calls == 1
    assert not r1.cache_hit
    assert sleeps == []

    monkeypatch.setattr(
        structured_extraction_service,
        "_get_groq_client",
        lambda: bench._make_offline_stub_client_factory(doc2)(),
    )
    r2 = bench.run_approach_a(doc2, cache_dir=tmp_path, use_cache=True, pacer=pacer)
    assert r2.llm_calls == 1
    assert not r2.cache_hit
    assert sleeps == [12.5]

    # Cache hit replay on doc1 must NOT sleep.
    def _never_called() -> Any:
        raise AssertionError("Cached replay should not invoke live client")

    monkeypatch.setattr(structured_extraction_service, "_get_groq_client", _never_called)
    r1_cached = bench.run_approach_a(doc1, cache_dir=tmp_path, use_cache=True, pacer=pacer)
    assert r1_cached.cache_hit is True
    assert sleeps == [12.5]

    # Approach C resolved locally (0 LLM calls) must NOT sleep.
    res_c = bench.run_approach_c(doc1, pacer=pacer)
    if res_c.llm_calls == 0:
        assert sleeps == [12.5]


def test_estimate_token_budget_scanned_and_unusable() -> None:
    doc = _scanned_invoice_doc()
    real_text = _paired_digital_text(doc)

    usable_est = bench.estimate_token_budget(
        [doc],
        ocr_fn=lambda **kw: _make_ocr_result(
            document_id=kw["document_id"],
            page_number=kw["page_number"],
            original_embedded_text=kw["original_embedded_text"],
            ocr_text=real_text,
        ),
    )
    assert usable_est["documents"] == 1
    assert usable_est["A_calls"] == 1
    assert usable_est["A_prompt_tokens_est"] > 0
    assert usable_est["B_calls"] == 0

    unusable_est = bench.estimate_token_budget(
        [doc],
        ocr_fn=lambda **kw: _make_ocr_result(
            document_id=kw["document_id"],
            page_number=kw["page_number"],
            original_embedded_text=kw["original_embedded_text"],
            ocr_text="",
        ),
    )
    assert unusable_est["A_calls"] == 0
    assert unusable_est["C_calls"] == 0
    assert unusable_est["combined_total_tokens_est"] == 0


def test_render_benchmark_markdown_scanned_and_both() -> None:
    docs_both = bench.load_benchmark_documents(variant="both", limit=2)
    text_doc = next(d for d in docs_both if d.variant == "text")
    scanned_doc = next(d for d in docs_both if d.variant == "scanned")
    real_text = _paired_digital_text(scanned_doc)

    loaded_scanned = bench._load_pdf_bundle(
        scanned_doc,
        ocr_fn=lambda **kw: _make_ocr_result(
            document_id=kw["document_id"],
            page_number=kw["page_number"],
            original_embedded_text=kw["original_embedded_text"],
            ocr_text=real_text,
        ),
    )

    scanned_b = bench.run_approach_b(scanned_doc, loaded_bundle=loaded_scanned)
    scanned_a = bench.run_approach_a(
        scanned_doc,
        loaded_bundle=loaded_scanned,
        client_factory=bench._make_offline_stub_client_factory(scanned_doc),
    )
    scanned_c = bench.run_approach_c(
        scanned_doc,
        loaded_bundle=loaded_scanned,
        client_factory=bench._make_offline_stub_client_factory(scanned_doc),
    )

    scanned_results: dict[bench.ApproachId, list[bench.DocumentRunResult]] = {
        "A_llm_only": [scanned_a],
        "B_deterministic_only": [scanned_b],
        "C_hybrid": [scanned_c],
    }
    scanned_summaries = {
        aid: bench.summarize_approach(aid, res)
        for aid, res in scanned_results.items()
    }
    scanned_md = bench.render_benchmark_markdown(
        documents=[scanned_doc],
        summaries=scanned_summaries,
        results_by_approach=scanned_results,
        model_name="openai/gpt-oss-20b",
        variant="scanned",
    )
    assert "# CACE Extraction Benchmark (`--variant scanned`)" in scanned_md
    assert "scanned/OCR PDFs" in scanned_md
    assert "Digital PDFs only (`--variant text`)" not in scanned_md

    text_b = bench.run_approach_b(text_doc)
    text_a = bench.run_approach_a(
        text_doc,
        client_factory=bench._make_offline_stub_client_factory(text_doc),
    )
    text_c = bench.run_approach_c(
        text_doc,
        client_factory=bench._make_offline_stub_client_factory(text_doc),
    )
    both_results: dict[bench.ApproachId, list[bench.DocumentRunResult]] = {
        "A_llm_only": [text_a, scanned_a],
        "B_deterministic_only": [text_b, scanned_b],
        "C_hybrid": [text_c, scanned_c],
    }
    both_summaries = {
        aid: bench.summarize_approach(aid, res)
        for aid, res in both_results.items()
    }
    both_md = bench.render_benchmark_markdown(
        documents=[text_doc, scanned_doc],
        summaries=both_summaries,
        results_by_approach=both_results,
        model_name="openai/gpt-oss-20b",
        variant="both",
    )
    assert "# CACE Extraction Benchmark (`--variant both`)" in both_md
    assert "### Breakdown by Variant (`text` vs. `scanned`)" in both_md
    assert "Digital PDFs (`text`)" in both_md
    assert "Scanned/OCR PDFs (`scanned`)" in both_md
