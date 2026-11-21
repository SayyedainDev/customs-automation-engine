"""Benchmark extraction approaches (A: LLM-only, B: Deterministic-only, C: Hybrid).

Step 1a: CLI skeleton, data contracts, and ground-truth loader.
Step 1b: Approach B (Deterministic-only), shared materialization via CACE's
production functions, shared field-level scorer reusing ``extraction_accuracy``,
and ``--dry-run`` summary reporting.
Step 1c: Approach C (Hybrid) calling CACE's production functions unmodified
with in-script Groq token capture (``_TokenCapturingGroqProxy``).
Step 1d: Approach A (LLM-only single-shot baseline), disk response caching
under ``backend/benchmark_cache/``, and pre-flight token estimation
(``--estimate-tokens``).
Flow 6: Scanned/OCR benchmark support (``--variant scanned`` and ``--variant both``),
OCR page caching (``ocr__<document_id>.json``), and ``--delay-seconds`` live API pacing.

Usage::

    python -m scripts.benchmark_extraction --variant text --estimate-tokens
    python -m scripts.benchmark_extraction --variant scanned --limit 4 --dry-run
    python -m scripts.benchmark_extraction --variant both --limit 4 --verify-offline
    python -m scripts.benchmark_extraction --variant scanned --limit 4 --delay-seconds 35
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import (  # noqa: E402
    StructuredExtractionConfigurationError,
    StructuredExtractionProviderError,
    StructuredExtractionRateLimitedError,
)
from app.schemas.multi_line_extraction import (  # noqa: E402
    MultiLineCommercialInvoiceExtraction,
    MultiLineInvoiceCandidates,
    MultiLinePackingListCandidates,
    MultiLinePackingListExtraction,
)
from app.schemas.ocr import OcrPageResult, OcrValidationStatus  # noqa: E402
from app.schemas.shipment_extraction import (  # noqa: E402
    ExtractedField,
    FieldValidationStatus,
    SourcePageReview,
)
from app.services import structured_extraction_service  # noqa: E402
from app.services.extraction.document_bundle import (  # noqa: E402
    DocumentTextBundle,
    StoredPage,
    page_has_useful_text,
    page_marked_text,
    page_word_lists,
)
from app.services.extraction.hybrid_orchestrator import (  # noqa: E402
    extract_invoice_hybrid,
    extract_packing_hybrid,
    invoice_gapfill_fields,
    packing_gapfill_fields,
)
from app.services.extraction.llm_gapfill import (  # noqa: E402
    GAPFILL_SYSTEM_PROMPT,
    build_context,
    build_user_prompt,
)
from app.services.extraction.ocr_extractor import (  # noqa: E402
    OcrConfigurationError,
    OcrExtractionError,
    ocr_pdf_page,
)
from app.services.extraction.pdf_extractor import extract_text_from_pdf  # noqa: E402
from app.services.extraction.regex_extractor import extract_document  # noqa: E402
from app.services.multi_line_shipment_service import (  # noqa: E402
    MULTI_LINE_INVOICE_SYSTEM_PROMPT,
    MULTI_LINE_INVOICE_USER_PROMPT_TEMPLATE,
    MULTI_LINE_PACKING_SYSTEM_PROMPT,
    MULTI_LINE_PACKING_USER_PROMPT_TEMPLATE,
    _apply_single_line_declared_weight_fallback,
    _apply_single_line_declared_weight_fallback_to_packing_list,
    _empty_invoice_candidates,
    _empty_packing_candidates,
    _extract_invoice_candidates_hybrid,
    _extract_packing_candidates_hybrid,
    _materialize_invoice,
    _materialize_packing_list,
    _single_shot_invoice_call,
    _single_shot_packing_call,
)
from app.services.structured_extraction_service import _groq_strict_schema  # noqa: E402
from app.services.validation.extraction_accuracy import _compare_value  # noqa: E402

FACTORY_ROOT = PROJECT_ROOT / "synthetic_factory"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "docs" / "BENCHMARK.md"
DEFAULT_CACHE_DIR = BACKEND_ROOT / "benchmark_cache"

DocumentVariant = Literal["text", "scanned"]
DocumentKind = Literal["commercial_invoice", "packing_list"]
ApproachId = Literal["A_llm_only", "B_deterministic_only", "C_hybrid"]
MaterializedExtraction = MultiLineCommercialInvoiceExtraction | MultiLinePackingListExtraction
OcrPageFn = Callable[..., OcrPageResult]

# Canonical required-field lists shared fairly across Approaches A, B, and C.
# Every field listed here exists in both ``expected_extraction_and_checks.json``
# and CACE's Pydantic candidate/extraction schemas (``MultiLineInvoiceCandidates``
# and ``MultiLinePackingListCandidates``), so no approach is scored on a field
# it has no schema slot to return.
INVOICE_HEADER_FIELDS: tuple[str, ...] = (
    "exporter_name",
    "buyer_name",
    "invoice_number",
    "invoice_date",
    "currency",
    "destination_country",
    "invoice_total",
    "declared_net_weight_total",
    "declared_gross_weight_total",
)

INVOICE_ITEM_FIELDS: tuple[str, ...] = (
    "line_number",
    "product_name",
    "pct_code",
    "quantity",
    "unit",
    "unit_price",
    "line_total",
    "net_weight",
    "gross_weight",
)

PACKING_HEADER_FIELDS: tuple[str, ...] = (
    "declared_net_weight_total",
    "declared_gross_weight_total",
)

PACKING_ITEM_FIELDS: tuple[str, ...] = (
    "line_number",
    "product_name",
    "pct_code",
    "quantity",
    "unit",
    "net_weight",
    "gross_weight",
    "package_count",
)

_DATE_FIELDS = frozenset({"invoice_date"})
_INT_FIELDS = frozenset({"line_number", "package_count"})
_DECIMAL_FIELDS = frozenset(
    {
        "invoice_total",
        "declared_net_weight_total",
        "declared_gross_weight_total",
        "quantity",
        "unit_price",
        "line_total",
        "net_weight",
        "gross_weight",
    }
)


@dataclass(frozen=True)
class ExpectedFieldSpec:
    """One ground-truth required field slot on a single benchmark document."""

    path: str
    field_name: str
    expected_value: str | int


@dataclass(frozen=True)
class BenchmarkDocument:
    """Immutable specification of one synthetic document and its ground truth."""

    scenario_id: str
    scenario_title: str
    document_type: DocumentKind
    variant: DocumentVariant
    pdf_path: Path
    expected_fields: tuple[ExpectedFieldSpec, ...]
    expected_item_count: int

    @property
    def document_id(self) -> str:
        return f"{self.scenario_id}__{self.document_type}__{self.variant}"


@dataclass(frozen=True)
class FieldScore:
    """Comparison outcome for a single required field under one approach."""

    path: str
    field_name: str
    expected: object
    extracted: object
    status: Literal["correct", "silent_error", "missing", "manual_review"]


@dataclass(frozen=True)
class DocumentRunResult:
    """Measured telemetry and field scores for one document under one approach."""

    approach: ApproachId
    document_id: str
    scenario_id: str
    document_type: DocumentKind
    latency_seconds: float
    prompt_tokens: int
    completion_tokens: int
    llm_calls: int
    cache_hit: bool
    provider_failed: bool
    error_message: str | None
    field_scores: tuple[FieldScore, ...]
    variant: DocumentVariant = "text"
    ocr_latency_seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def total_fields(self) -> int:
        return len(self.field_scores)

    @property
    def correct_fields(self) -> int:
        return sum(1 for score in self.field_scores if score.status == "correct")

    @property
    def silent_error_fields(self) -> int:
        return sum(1 for score in self.field_scores if score.status == "silent_error")

    @property
    def missing_fields(self) -> int:
        return sum(1 for score in self.field_scores if score.status == "missing")

    @property
    def manual_review_fields(self) -> int:
        return sum(1 for score in self.field_scores if score.status == "manual_review")

    @property
    def has_manual_review(self) -> bool:
        return self.manual_review_fields > 0


@dataclass(frozen=True)
class LoadedBundle:
    """Extracted document bundle paired with PDF/OCR load timing and cache metadata."""

    bundle: DocumentTextBundle
    pdf_read_seconds: float = 0.0
    ocr_latency_seconds: float = 0.0
    ocr_cache_hit: bool = False
    ocr_pages_count: int = 0


@dataclass(frozen=True)
class ApproachSummary:
    """Aggregated metrics across all evaluated documents for one approach."""

    approach: ApproachId
    documents_count: int
    total_fields: int
    correct_fields: int
    silent_error_fields: int
    missing_fields: int
    manual_review_fields: int
    accuracy: float
    silent_error_rate: float
    abstention_rate: float
    avg_tokens_per_doc: float
    total_tokens: int
    avg_latency_seconds: float
    median_latency_seconds: float
    max_latency_seconds: float
    docs_with_manual_review: int
    total_llm_calls: int
    avg_llm_calls_per_doc: float
    pct_docs_needing_llm: float
    provider_failures: int


def _pdf_filename(document_type: DocumentKind, variant: DocumentVariant) -> str:
    prefix = (
        "synthetic_commercial_invoice"
        if document_type == "commercial_invoice"
        else "synthetic_packing_list"
    )
    return f"{prefix}_{variant}.pdf"


def _collect_expected_fields(
    document_type: DocumentKind,
    payload: dict[str, Any],
) -> tuple[tuple[ExpectedFieldSpec, ...], int]:
    """Extract non-null required ground-truth fields in deterministic order."""
    specs: list[ExpectedFieldSpec] = []
    if document_type == "commercial_invoice":
        header_fields = INVOICE_HEADER_FIELDS
        item_fields = INVOICE_ITEM_FIELDS
        items_key = "line_items"
    else:
        header_fields = PACKING_HEADER_FIELDS
        item_fields = PACKING_ITEM_FIELDS
        items_key = "items"

    for field_name in header_fields:
        value = payload.get(field_name)
        if value is not None:
            specs.append(
                ExpectedFieldSpec(
                    path=f"header.{field_name}",
                    field_name=field_name,
                    expected_value=value,
                )
            )

    raw_items = payload.get(items_key) or []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        for field_name in item_fields:
            value = item.get(field_name)
            if value is not None:
                specs.append(
                    ExpectedFieldSpec(
                        path=f"{items_key}[{index}].{field_name}",
                        field_name=field_name,
                        expected_value=value,
                    )
                )

    return tuple(specs), len(raw_items)


def _resolve_variants(variant: str) -> tuple[DocumentVariant, ...]:
    if variant == "text":
        return ("text",)
    if variant == "scanned":
        return ("scanned",)
    if variant == "both":
        return ("text", "scanned")
    raise ValueError(
        f"Unsupported --variant {variant!r}; expected 'text', 'scanned', or 'both'."
    )


def load_benchmark_documents(
    *,
    factory_root: Path = FACTORY_ROOT,
    variant: str = "text",
    limit: int | None = None,
    scenario_filter: str | None = None,
) -> list[BenchmarkDocument]:
    """Load synthetic_factory scenarios paired with expected extraction JSON."""
    variants_to_load = _resolve_variants(variant)
    if not factory_root.is_dir():
        raise FileNotFoundError(f"synthetic_factory directory not found: {factory_root}")

    documents: list[BenchmarkDocument] = []
    ground_truth_paths = sorted(factory_root.glob("*/expected_extraction_and_checks.json"))

    for gt_path in ground_truth_paths:
        scenario_dir = gt_path.parent
        scenario_id = scenario_dir.name
        if scenario_filter and scenario_filter not in scenario_id:
            continue

        raw_gt = json.loads(gt_path.read_text(encoding="utf-8"))
        scenario_title = str(raw_gt.get("scenario") or scenario_id)

        doc_types: tuple[DocumentKind, ...] = ("commercial_invoice", "packing_list")
        for typed_doc in doc_types:
            section = raw_gt.get(typed_doc)
            if not isinstance(section, dict):
                continue

            expected_fields, item_count = _collect_expected_fields(typed_doc, section)
            for active_variant in variants_to_load:
                pdf_path = scenario_dir / _pdf_filename(typed_doc, active_variant)
                if not pdf_path.is_file():
                    continue

                documents.append(
                    BenchmarkDocument(
                        scenario_id=scenario_id,
                        scenario_title=scenario_title,
                        document_type=typed_doc,
                        variant=active_variant,
                        pdf_path=pdf_path,
                        expected_fields=expected_fields,
                        expected_item_count=item_count,
                    )
                )
                if limit is not None and len(documents) >= limit:
                    return documents

    return documents


_SHORT_OCR_NOTE = "OCR text is too short to be useful for structured extraction."


def _ocr_cache_file_path(cache_dir: Path, doc: BenchmarkDocument) -> Path:
    return cache_dir / f"ocr__{doc.document_id}.json"


def _load_cached_ocr_entry(
    cache_dir: Path | None,
    doc: BenchmarkDocument,
) -> tuple[dict[int, OcrPageResult], float] | None:
    if cache_dir is None:
        return None
    path = _ocr_cache_file_path(cache_dir, doc)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw_pages = data.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        return None
    by_page: dict[int, OcrPageResult] = {}
    try:
        for item in raw_pages:
            parsed = OcrPageResult.model_validate(item)
            if parsed.validation_status == OcrValidationStatus.FAILED:
                return None
            by_page[parsed.page_number] = parsed
    except Exception:
        return None
    recorded_latency = float(data.get("recorded_ocr_latency_seconds") or 0.0)
    return by_page, recorded_latency


def _save_cached_ocr_entry(
    cache_dir: Path | None,
    doc: BenchmarkDocument,
    results: list[OcrPageResult],
    ocr_latency_seconds: float,
) -> None:
    if (
        cache_dir is None
        or not results
        or any(r.validation_status == OcrValidationStatus.FAILED for r in results)
    ):
        return
    settings = get_settings()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _ocr_cache_file_path(cache_dir, doc)
    payload = {
        "document_id": doc.document_id,
        "scenario_id": doc.scenario_id,
        "document_type": doc.document_type,
        "variant": doc.variant,
        "ocr_dpi": settings.ocr_dpi,
        "ocr_language": settings.ocr_language,
        "ocr_psm": settings.ocr_page_segmentation_mode,
        "recorded_ocr_latency_seconds": round(ocr_latency_seconds, 6),
        "pages": [result.model_dump(mode="json") for result in results],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _normalize_ocr_result(result: OcrPageResult) -> OcrPageResult:
    if page_has_useful_text(result.ocr_text):
        return result
    updated_note = (
        result.validation_note
        if _SHORT_OCR_NOTE in result.validation_note
        else f"{result.validation_note} {_SHORT_OCR_NOTE}".strip()
    )
    return result.model_copy(
        update={
            "validation_status": OcrValidationStatus.MANUAL_REVIEW,
            "validation_note": updated_note,
        }
    )


def _load_pdf_bundle(
    doc: BenchmarkDocument,
    *,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    ocr_fn: OcrPageFn | None = None,
) -> LoadedBundle:
    """Build an in-memory ``DocumentTextBundle`` for digital or scanned PDFs."""
    doc_uuid = uuid5(NAMESPACE_URL, doc.document_id)
    pdf_started = time.perf_counter()
    pdf_result = extract_text_from_pdf(doc.pdf_path)
    pdf_read_seconds = time.perf_counter() - pdf_started

    pages = [
        StoredPage(
            page_number=page.page_number,
            text=page.text,
            original_embedded_text=page.text,
            extraction_method="pdf_embedded_text",
            words=list(page.words),
        )
        for page in pdf_result.pages
    ]
    reviews = [
        SourcePageReview(
            document_id=doc_uuid,
            document_type=doc.document_type,
            page_number=page.page_number,
            character_count=len(page.text),
            requires_ocr=not page_has_useful_text(page.text),
            review_note=(
                "No useful embedded text was found; OCR is required."
                if not page_has_useful_text(page.text)
                else "Embedded PDF text is available for structured extraction."
            ),
        )
        for page in pages
    ]

    pages_needing_ocr = [review.page_number for review in reviews if review.requires_ocr]
    if not pages_needing_ocr:
        return LoadedBundle(
            bundle=DocumentTextBundle(
                document_id=doc_uuid,
                document_type=doc.document_type,
                pages=pages,
                reviews=reviews,
            ),
            pdf_read_seconds=pdf_read_seconds,
        )

    cached_ocr = (
        _load_cached_ocr_entry(cache_dir, doc)
        if use_cache and cache_dir is not None
        else None
    )
    cached_by_page = (
        cached_ocr[0]
        if cached_ocr is not None and all(p in cached_ocr[0] for p in pages_needing_ocr)
        else None
    )
    ocr_cache_hit = cached_by_page is not None
    active_ocr_fn = ocr_fn or ocr_pdf_page

    pages_by_number = {page.page_number: page for page in pages}
    updated_reviews: list[SourcePageReview] = []
    ocr_results: list[OcrPageResult] = []
    ocr_started = time.perf_counter()

    for review in reviews:
        page = pages_by_number[review.page_number]
        if not review.requires_ocr:
            updated_reviews.append(review)
            continue

        try:
            if cached_by_page is not None:
                result = cached_by_page[page.page_number]
            else:
                result = active_ocr_fn(
                    pdf_path=doc.pdf_path,
                    document_id=doc_uuid,
                    page_number=page.page_number,
                    original_embedded_text=page.original_embedded_text,
                )
            result = _normalize_ocr_result(result)
            ocr_results.append(result)
            pages_by_number[page.page_number] = page.model_copy(
                update={
                    "text": result.ocr_text,
                    "extraction_method": result.extraction_method,
                    "ocr_confidence": result.ocr_confidence,
                    "ocr_validation_status": result.validation_status,
                    "words": [],
                }
            )
            updated_reviews.append(
                review.model_copy(
                    update={
                        "ocr_attempted": True,
                        "ocr_engine": result.ocr_engine,
                        "ocr_confidence": result.ocr_confidence,
                        "ocr_validation_status": result.validation_status,
                        "ocr_note": result.validation_note,
                        "review_note": (
                            "Embedded text was unavailable; the page was "
                            "processed by Tesseract OCR."
                        ),
                    }
                )
            )
        except OcrConfigurationError:
            raise
        except OcrExtractionError as exc:
            failed_result = OcrPageResult(
                document_id=doc_uuid,
                page_number=page.page_number,
                original_embedded_text=page.original_embedded_text,
                ocr_text="",
                ocr_confidence=Decimal("0"),
                validation_status=OcrValidationStatus.FAILED,
                validation_note=str(exc),
            )
            ocr_results.append(failed_result)
            pages_by_number[page.page_number] = page.model_copy(
                update={
                    "text": "",
                    "ocr_confidence": Decimal("0"),
                    "ocr_validation_status": OcrValidationStatus.FAILED,
                    "words": [],
                }
            )
            updated_reviews.append(
                review.model_copy(
                    update={
                        "ocr_attempted": True,
                        "ocr_engine": "tesseract",
                        "ocr_confidence": Decimal("0"),
                        "ocr_validation_status": OcrValidationStatus.FAILED,
                        "ocr_note": str(exc),
                        "review_note": (
                            "Embedded text was unavailable and OCR failed; "
                            "manual review is required."
                        ),
                    }
                )
            )

    ocr_elapsed = (
        cached_ocr[1]
        if ocr_cache_hit and cached_ocr is not None
        else (time.perf_counter() - ocr_started)
    )
    if not ocr_cache_hit and cache_dir is not None:
        _save_cached_ocr_entry(cache_dir, doc, ocr_results, ocr_elapsed)

    bundle = DocumentTextBundle(
        document_id=doc_uuid,
        document_type=doc.document_type,
        pages=[pages_by_number[page_num] for page_num in sorted(pages_by_number)],
        reviews=updated_reviews,
    )
    return LoadedBundle(
        bundle=bundle,
        pdf_read_seconds=pdf_read_seconds,
        ocr_latency_seconds=ocr_elapsed,
        ocr_cache_hit=ocr_cache_hit,
        ocr_pages_count=len(ocr_results),
    )


def _load_text_pdf_bundle(doc: BenchmarkDocument) -> DocumentTextBundle:
    """Backward-compatible wrapper returning only the loaded ``DocumentTextBundle``."""
    return _load_pdf_bundle(doc).bundle


def _materialize_candidates(
    doc: BenchmarkDocument,
    candidates: MultiLineInvoiceCandidates | MultiLinePackingListCandidates,
    bundle: DocumentTextBundle,
) -> MaterializedExtraction:
    """Apply CACE's exact downstream provenance gate and single-line weight fallback."""
    if doc.document_type == "commercial_invoice":
        invoice_candidates = cast(MultiLineInvoiceCandidates, candidates)
        materialized_inv = _materialize_invoice(invoice_candidates, bundle)
        return _apply_single_line_declared_weight_fallback(materialized_inv)

    packing_candidates = cast(MultiLinePackingListCandidates, candidates)
    materialized_pl = _materialize_packing_list(packing_candidates, bundle)
    return _apply_single_line_declared_weight_fallback_to_packing_list(materialized_pl)


def _coerce_expected_value(field_name: str, raw_value: str | int) -> object:
    """Coerce JSON string/int ground truth into the schema's Python type."""
    if field_name in _DATE_FIELDS and isinstance(raw_value, str):
        return date.fromisoformat(raw_value.strip())
    if field_name in _INT_FIELDS:
        return int(raw_value)
    if field_name in _DECIMAL_FIELDS:
        try:
            return Decimal(str(raw_value))
        except InvalidOperation:
            return raw_value
    return str(raw_value)


def _resolve_extracted_field(
    extraction: MaterializedExtraction,
    path: str,
) -> ExtractedField[Any] | None:
    """Look up the materialized ``ExtractedField`` at a canonical ground-truth path."""
    if path.startswith("header."):
        field_name = path.removeprefix("header.")
        return cast(ExtractedField[Any] | None, getattr(extraction, field_name, None))

    items_attr = (
        "line_items"
        if isinstance(extraction, MultiLineCommercialInvoiceExtraction)
        else "items"
    )
    items = getattr(extraction, items_attr)
    prefix, field_name = path.split(".", 1)
    index_str = prefix.split("[", 1)[1].rstrip("]")
    index = int(index_str)
    if index >= len(items):
        return None
    return cast(ExtractedField[Any] | None, getattr(items[index], field_name, None))


def score_extraction(
    doc: BenchmarkDocument,
    extraction: MaterializedExtraction | None,
) -> tuple[FieldScore, ...]:
    """Score extracted fields identically across all approaches using ``_compare_value``."""
    scores: list[FieldScore] = []
    for spec in doc.expected_fields:
        typed_expected = _coerce_expected_value(spec.field_name, spec.expected_value)
        if extraction is None:
            scores.append(
                FieldScore(
                    path=spec.path,
                    field_name=spec.field_name,
                    expected=typed_expected,
                    extracted=None,
                    status="missing",
                )
            )
            continue

        actual_field = _resolve_extracted_field(extraction, spec.path)
        if actual_field is None:
            scores.append(
                FieldScore(
                    path=spec.path,
                    field_name=spec.field_name,
                    expected=typed_expected,
                    extracted=None,
                    status="missing",
                )
            )
            continue

        if actual_field.validation_status == FieldValidationStatus.MANUAL_REVIEW:
            scores.append(
                FieldScore(
                    path=spec.path,
                    field_name=spec.field_name,
                    expected=typed_expected,
                    extracted=actual_field.value,
                    status="manual_review",
                )
            )
            continue

        if actual_field.value is None:
            scores.append(
                FieldScore(
                    path=spec.path,
                    field_name=spec.field_name,
                    expected=typed_expected,
                    extracted=None,
                    status="missing",
                )
            )
            continue

        verdict = _compare_value(spec.field_name, typed_expected, actual_field.value)
        status: Literal["correct", "silent_error"] = (
            "correct" if verdict == "correct" else "silent_error"
        )
        scores.append(
            FieldScore(
                path=spec.path,
                field_name=spec.field_name,
                expected=typed_expected,
                extracted=actual_field.value,
                status=status,
            )
        )

    return tuple(scores)


def _resolve_loaded_bundle(
    doc: BenchmarkDocument,
    *,
    loaded_bundle: LoadedBundle | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    ocr_fn: OcrPageFn | None = None,
) -> LoadedBundle:
    if loaded_bundle is not None:
        return loaded_bundle
    return _load_pdf_bundle(
        doc,
        cache_dir=cache_dir,
        use_cache=use_cache,
        ocr_fn=ocr_fn,
    )


def _empty_candidates(
    doc: BenchmarkDocument,
    note: str,
) -> MultiLineInvoiceCandidates | MultiLinePackingListCandidates:
    if doc.document_type == "commercial_invoice":
        return _empty_invoice_candidates(note)
    return _empty_packing_candidates(note)


def run_approach_b(
    doc: BenchmarkDocument,
    *,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    loaded_bundle: LoadedBundle | None = None,
    ocr_fn: OcrPageFn | None = None,
) -> DocumentRunResult:
    """Approach B: Deterministic-only (PyMuPDF/OCR text + regex + coordinates, 0 LLM tokens)."""
    loaded = _resolve_loaded_bundle(
        doc,
        loaded_bundle=loaded_bundle,
        cache_dir=cache_dir,
        use_cache=use_cache,
        ocr_fn=ocr_fn,
    )
    bundle = loaded.bundle
    started = time.perf_counter()

    if not bundle.useful_pages:
        candidates: MultiLineInvoiceCandidates | MultiLinePackingListCandidates = (
            _empty_candidates(
                doc, "Every page requires OCR; no usable text was extracted."
            )
        )
    else:
        marked_text = page_marked_text(bundle)
        word_lists = page_word_lists(bundle)
        if doc.document_type == "commercial_invoice":
            candidates, _telemetry = extract_invoice_hybrid(
                marked_text,
                page_words=word_lists,
                document_ref=doc.document_id,
            )
        else:
            candidates, _telemetry = extract_packing_hybrid(
                marked_text,
                page_words=word_lists,
                document_ref=doc.document_id,
            )

    materialized = _materialize_candidates(doc, candidates, bundle)
    scores = score_extraction(doc, materialized)
    elapsed = (
        loaded.pdf_read_seconds
        + loaded.ocr_latency_seconds
        + (time.perf_counter() - started)
    )

    return DocumentRunResult(
        approach="B_deterministic_only",
        document_id=doc.document_id,
        scenario_id=doc.scenario_id,
        document_type=doc.document_type,
        latency_seconds=elapsed,
        prompt_tokens=0,
        completion_tokens=0,
        llm_calls=0,
        cache_hit=False,
        provider_failed=False,
        error_message=None,
        field_scores=scores,
        variant=doc.variant,
        ocr_latency_seconds=loaded.ocr_latency_seconds,
    )


class _LiveCallPacer:
    """Sleeps between consecutive uncached live Groq calls without inflating latency."""

    def __init__(
        self,
        delay_seconds: float,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self._sleep_fn = sleep_fn
        self._has_called = False
        self.sleep_count = 0
        self.total_slept_seconds = 0.0

    def before_live_call(self) -> float:
        if self.delay_seconds <= 0.0:
            self._has_called = True
            return 0.0
        if not self._has_called:
            self._has_called = True
            return 0.0
        t0 = time.perf_counter()
        self._sleep_fn(self.delay_seconds)
        actual = time.perf_counter() - t0
        self.sleep_count += 1
        self.total_slept_seconds += self.delay_seconds
        return actual


@dataclass
class _TokenLedger:
    """Mutable per-document accumulator filled by ``_TokenCapturingGroqProxy``."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    rate_limited_429: bool = False
    pacer_sleep_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    raw_responses: list[dict[str, Any]] = field(default_factory=list)


class _TokenCapturingCompletions:
    """Wraps ``client.chat.completions`` to capture Groq token usage in-script."""

    def __init__(
        self,
        real_completions: Any,
        ledger: _TokenLedger,
        pacer: _LiveCallPacer | None = None,
    ) -> None:
        self._real = real_completions
        self._ledger = ledger
        self._pacer = pacer

    def create(self, **kwargs: Any) -> Any:
        if self._pacer is not None:
            self._ledger.pacer_sleep_seconds += self._pacer.before_live_call()
        self._ledger.calls += 1
        try:
            response = self._real.create(**kwargs)
        except Exception as exc:
            self._ledger.errors.append(f"{type(exc).__name__}: {exc}")
            if getattr(exc, "status_code", None) == 429:
                self._ledger.rate_limited_429 = True
            raise

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        self._ledger.prompt_tokens += prompt_tokens
        self._ledger.completion_tokens += completion_tokens
        choices = getattr(response, "choices", None) or []
        content = (
            getattr(getattr(choices[0], "message", None), "content", None)
            if choices
            else None
        )
        self._ledger.raw_responses.append(
            {
                "model": kwargs.get("model"),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "content": content,
            }
        )
        return response


class _TokenCapturingGroqProxy:
    """Thin client proxy preserving Groq's interface without editing ``app/``."""

    def __init__(
        self,
        real_client: Any,
        ledger: _TokenLedger,
        pacer: _LiveCallPacer | None = None,
    ) -> None:
        self._real = real_client
        self.chat = SimpleNamespace(
            completions=_TokenCapturingCompletions(
                real_client.chat.completions,
                ledger,
                pacer=pacer,
            )
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@contextlib.contextmanager
def _capture_groq_tokens(
    *,
    client_factory: Callable[[], Any] | None = None,
    pacer: _LiveCallPacer | None = None,
) -> Iterator[_TokenLedger]:
    """Patch ``structured_extraction_service._get_groq_client`` for one run."""
    ledger = _TokenLedger()
    original_getter = structured_extraction_service._get_groq_client
    base_factory = client_factory or original_getter

    def _wrapped_getter() -> Any:
        return _TokenCapturingGroqProxy(base_factory(), ledger, pacer=pacer)

    structured_extraction_service._get_groq_client = _wrapped_getter
    try:
        yield ledger
    finally:
        structured_extraction_service._get_groq_client = original_getter


def _cache_file_path(cache_dir: Path, approach: ApproachId, doc: BenchmarkDocument) -> Path:
    return cache_dir / f"{approach}__{doc.document_id}.json"


def _load_cached_llm_entry(
    cache_dir: Path | None,
    approach: ApproachId,
    doc: BenchmarkDocument,
) -> dict[str, Any] | None:
    if cache_dir is None:
        return None
    path = _cache_file_path(cache_dir, approach, doc)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    responses = data.get("responses")
    if not isinstance(responses, list) or not responses:
        return None
    return data


def _save_cached_llm_entry(
    cache_dir: Path | None,
    approach: ApproachId,
    doc: BenchmarkDocument,
    ledger: _TokenLedger,
    latency_seconds: float,
) -> None:
    if cache_dir is None or not ledger.raw_responses or ledger.rate_limited_429 or ledger.errors:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_file_path(cache_dir, approach, doc)
    payload = {
        "approach": approach,
        "document_id": doc.document_id,
        "scenario_id": doc.scenario_id,
        "document_type": doc.document_type,
        "variant": doc.variant,
        "llm_calls": ledger.calls,
        "prompt_tokens": ledger.prompt_tokens,
        "completion_tokens": ledger.completion_tokens,
        "recorded_api_latency_seconds": round(latency_seconds, 6),
        "responses": ledger.raw_responses,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _make_cached_client_factory(cached_entry: dict[str, Any]) -> Callable[[], Any]:
    """Replay cached raw Groq completions sequentially with 0 network calls."""
    responses = iter(cached_entry["responses"])

    class _CachedCompletions:
        def create(self, **kwargs: Any) -> Any:
            item = next(responses)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=item.get("content"))
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=int(item.get("prompt_tokens") or 0),
                    completion_tokens=int(item.get("completion_tokens") or 0),
                ),
            )

    cached_client = SimpleNamespace(chat=SimpleNamespace(completions=_CachedCompletions()))
    return lambda: cached_client


def _make_offline_stub_client_factory(doc: BenchmarkDocument) -> Callable[[], Any]:
    """Deterministic local stub verifying Approach A and C token capture at 0 API cost."""

    class _StubCompletions:
        def create(self, **kwargs: Any) -> Any:
            user_text = ""
            for msg in kwargs.get("messages") or ():
                if msg.get("role") == "user":
                    user_text = str(msg.get("content") or "")
            schema_name = (
                kwargs.get("response_format", {})
                .get("json_schema", {})
                .get("name", "")
            )
            schema_props = (
                kwargs.get("response_format", {})
                .get("json_schema", {})
                .get("schema", {})
                .get("properties", {})
            )
            if schema_name == "hybrid_gapfill_fields":
                payload: dict[str, Any] = {
                    key: ("5" if "package" in key and "5" in user_text else None)
                    for key in schema_props
                }
                completion_tokens = 16
            else:
                def _stub_field(val: Any = None, note: str = "offline stub") -> dict[str, Any]:
                    return {
                        "value": val,
                        "source_page": 1 if val is not None else None,
                        "confidence": 0.9 if val is not None else 0.0,
                        "validation_status": "verified" if val is not None else "manual_review",
                        "validation_note": note,
                    }

                items_key = "line_items" if doc.document_type == "commercial_invoice" else "items"
                payload = {
                    k: _stub_field(None)
                    for k in schema_props
                    if k != items_key
                }
                if items_key in schema_props:
                    payload[items_key] = []
                completion_tokens = 64

            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(
                    prompt_tokens=max(1, len(user_text) // 4),
                    completion_tokens=completion_tokens,
                ),
            )

    stub_client = SimpleNamespace(chat=SimpleNamespace(completions=_StubCompletions()))
    return lambda: stub_client


def run_approach_c(
    doc: BenchmarkDocument,
    *,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    client_factory: Callable[[], Any] | None = None,
    loaded_bundle: LoadedBundle | None = None,
    ocr_fn: OcrPageFn | None = None,
    pacer: _LiveCallPacer | None = None,
) -> DocumentRunResult:
    """Approach C: CACE's production Hybrid pipeline with Groq usage capture."""
    loaded = _resolve_loaded_bundle(
        doc,
        loaded_bundle=loaded_bundle,
        cache_dir=cache_dir,
        use_cache=use_cache,
        ocr_fn=ocr_fn,
    )
    bundle = loaded.bundle
    started = time.perf_counter()

    if not bundle.useful_pages:
        candidates = _empty_candidates(
            doc, "Every page requires OCR; no usable text was extracted."
        )
        empty_materialized = _materialize_candidates(doc, candidates, bundle)
        scores = score_extraction(doc, empty_materialized)
        elapsed = (
            loaded.pdf_read_seconds
            + loaded.ocr_latency_seconds
            + (time.perf_counter() - started)
        )
        return DocumentRunResult(
            approach="C_hybrid",
            document_id=doc.document_id,
            scenario_id=doc.scenario_id,
            document_type=doc.document_type,
            latency_seconds=elapsed,
            prompt_tokens=0,
            completion_tokens=0,
            llm_calls=0,
            cache_hit=False,
            provider_failed=False,
            error_message=None,
            field_scores=scores,
            variant=doc.variant,
            ocr_latency_seconds=loaded.ocr_latency_seconds,
        )

    marked_text = page_marked_text(bundle)
    cached_entry = (
        _load_cached_llm_entry(cache_dir, "C_hybrid", doc)
        if use_cache and client_factory is None
        else None
    )
    active_factory = (
        _make_cached_client_factory(cached_entry)
        if cached_entry is not None
        else client_factory
    )
    active_pacer = None if cached_entry is not None else pacer

    provider_failed = False
    error_message: str | None = None
    materialized: MaterializedExtraction | None = None

    with _capture_groq_tokens(client_factory=active_factory, pacer=active_pacer) as ledger:
        try:
            if doc.document_type == "commercial_invoice":
                candidates, telemetry = _extract_invoice_candidates_hybrid(bundle, marked_text)
            else:
                candidates, telemetry = _extract_packing_candidates_hybrid(bundle, marked_text)
            materialized = _materialize_candidates(doc, candidates, bundle)
        except StructuredExtractionRateLimitedError as exc:
            ledger.rate_limited_429 = True
            provider_failed = True
            error_message = str(exc)
        except (StructuredExtractionProviderError, StructuredExtractionConfigurationError) as exc:
            provider_failed = True
            error_message = str(exc)
        else:
            if ledger.rate_limited_429 or ledger.errors:
                provider_failed = True
                error_message = "; ".join(ledger.errors)
            elif any("failed" in note.lower() for note in telemetry.notes):
                provider_failed = True
                error_message = "; ".join(telemetry.notes)

    extraction_elapsed = max(
        0.0, (time.perf_counter() - started) - ledger.pacer_sleep_seconds
    )
    if cached_entry is None and client_factory is None and ledger.calls > 0:
        _save_cached_llm_entry(cache_dir, "C_hybrid", doc, ledger, extraction_elapsed)

    reported_extraction = (
        float(cached_entry.get("recorded_api_latency_seconds") or extraction_elapsed)
        if cached_entry is not None
        else extraction_elapsed
    )
    reported_latency = (
        loaded.pdf_read_seconds + loaded.ocr_latency_seconds + reported_extraction
    )
    scores = score_extraction(doc, materialized)

    return DocumentRunResult(
        approach="C_hybrid",
        document_id=doc.document_id,
        scenario_id=doc.scenario_id,
        document_type=doc.document_type,
        latency_seconds=reported_latency,
        prompt_tokens=ledger.prompt_tokens,
        completion_tokens=ledger.completion_tokens,
        llm_calls=ledger.calls,
        cache_hit=cached_entry is not None,
        provider_failed=provider_failed,
        error_message=error_message,
        field_scores=scores,
        variant=doc.variant,
        ocr_latency_seconds=loaded.ocr_latency_seconds,
    )


def run_approach_a(
    doc: BenchmarkDocument,
    *,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    client_factory: Callable[[], Any] | None = None,
    loaded_bundle: LoadedBundle | None = None,
    ocr_fn: OcrPageFn | None = None,
    pacer: _LiveCallPacer | None = None,
) -> DocumentRunResult:
    """Approach A: Single-shot full-document Groq baseline (no deterministic path)."""
    loaded = _resolve_loaded_bundle(
        doc,
        loaded_bundle=loaded_bundle,
        cache_dir=cache_dir,
        use_cache=use_cache,
        ocr_fn=ocr_fn,
    )
    bundle = loaded.bundle
    started = time.perf_counter()

    if not bundle.useful_pages:
        candidates: MultiLineInvoiceCandidates | MultiLinePackingListCandidates = (
            _empty_candidates(
                doc, "Every page requires OCR; no usable text was extracted."
            )
        )
        empty_materialized = _materialize_candidates(doc, candidates, bundle)
        scores = score_extraction(doc, empty_materialized)
        elapsed = (
            loaded.pdf_read_seconds
            + loaded.ocr_latency_seconds
            + (time.perf_counter() - started)
        )
        return DocumentRunResult(
            approach="A_llm_only",
            document_id=doc.document_id,
            scenario_id=doc.scenario_id,
            document_type=doc.document_type,
            latency_seconds=elapsed,
            prompt_tokens=0,
            completion_tokens=0,
            llm_calls=0,
            cache_hit=False,
            provider_failed=False,
            error_message=None,
            field_scores=scores,
            variant=doc.variant,
            ocr_latency_seconds=loaded.ocr_latency_seconds,
        )

    marked_text = page_marked_text(bundle)
    cached_entry = (
        _load_cached_llm_entry(cache_dir, "A_llm_only", doc)
        if use_cache and client_factory is None
        else None
    )
    active_factory = (
        _make_cached_client_factory(cached_entry)
        if cached_entry is not None
        else client_factory
    )
    active_pacer = None if cached_entry is not None else pacer

    provider_failed = False
    error_message: str | None = None
    materialized: MaterializedExtraction | None = None

    with _capture_groq_tokens(client_factory=active_factory, pacer=active_pacer) as ledger:
        try:
            if doc.document_type == "commercial_invoice":
                candidates = _single_shot_invoice_call(marked_text)
            else:
                candidates = _single_shot_packing_call(marked_text)
            materialized = _materialize_candidates(doc, candidates, bundle)
        except StructuredExtractionRateLimitedError as exc:
            ledger.rate_limited_429 = True
            provider_failed = True
            error_message = str(exc)
        except (StructuredExtractionProviderError, StructuredExtractionConfigurationError) as exc:
            provider_failed = True
            error_message = str(exc)
        else:
            if ledger.rate_limited_429 or ledger.errors:
                provider_failed = True
                error_message = "; ".join(ledger.errors)

    extraction_elapsed = max(
        0.0, (time.perf_counter() - started) - ledger.pacer_sleep_seconds
    )
    if cached_entry is None and client_factory is None and ledger.calls > 0:
        _save_cached_llm_entry(cache_dir, "A_llm_only", doc, ledger, extraction_elapsed)

    reported_extraction = (
        float(cached_entry.get("recorded_api_latency_seconds") or extraction_elapsed)
        if cached_entry is not None
        else extraction_elapsed
    )
    reported_latency = (
        loaded.pdf_read_seconds + loaded.ocr_latency_seconds + reported_extraction
    )
    scores = score_extraction(doc, materialized)

    return DocumentRunResult(
        approach="A_llm_only",
        document_id=doc.document_id,
        scenario_id=doc.scenario_id,
        document_type=doc.document_type,
        latency_seconds=reported_latency,
        prompt_tokens=ledger.prompt_tokens,
        completion_tokens=ledger.completion_tokens,
        llm_calls=ledger.calls,
        cache_hit=cached_entry is not None,
        provider_failed=provider_failed,
        error_message=error_message,
        field_scores=scores,
        variant=doc.variant,
        ocr_latency_seconds=loaded.ocr_latency_seconds,
    )


def estimate_token_budget(
    documents: list[BenchmarkDocument],
    *,
    loaded_bundles: dict[str, LoadedBundle] | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    ocr_fn: OcrPageFn | None = None,
) -> dict[str, Any]:
    """Estimate pre-flight Groq calls and tokens at 0 API cost (~4 chars/token)."""
    inv_schema_chars = len(json.dumps(_groq_strict_schema(MultiLineInvoiceCandidates)))
    pl_schema_chars = len(json.dumps(_groq_strict_schema(MultiLinePackingListCandidates)))

    a_calls = 0
    a_prompt_tokens = 0
    a_completion_est = 0
    c_calls = 0
    c_prompt_tokens = 0
    c_completion_est = 0

    for doc in documents:
        loaded = _resolve_loaded_bundle(
            doc,
            loaded_bundle=(loaded_bundles or {}).get(doc.document_id),
            cache_dir=cache_dir,
            use_cache=use_cache,
            ocr_fn=ocr_fn,
        )
        bundle = loaded.bundle
        if not bundle.useful_pages:
            continue

        marked_text = page_marked_text(bundle)
        word_lists = page_word_lists(bundle)

        if doc.document_type == "commercial_invoice":
            a_sys = MULTI_LINE_INVOICE_SYSTEM_PROMPT
            a_usr = MULTI_LINE_INVOICE_USER_PROMPT_TEMPLATE.format(document_pages=marked_text)
            a_schema_chars = inv_schema_chars
            gap_allowed = set(invoice_gapfill_fields())
            est_out_a = 500 + 250 * max(1, doc.expected_item_count)
        else:
            a_sys = MULTI_LINE_PACKING_SYSTEM_PROMPT
            a_usr = MULTI_LINE_PACKING_USER_PROMPT_TEMPLATE.format(document_pages=marked_text)
            a_schema_chars = pl_schema_chars
            gap_allowed = set(packing_gapfill_fields())
            est_out_a = 350 + 200 * max(1, doc.expected_item_count)

        # Conservative ~4 chars/token estimate for prompt + strict JSON schema + output
        a_calls += 1
        a_prompt_tokens += (len(a_sys) + len(a_usr) + a_schema_chars + 3) // 4
        a_completion_est += est_out_a

        extraction = extract_document(marked_text, page_words=word_lists)
        if not extraction.line_items:
            c_calls += 1
            c_prompt_tokens += (len(a_sys) + len(a_usr) + a_schema_chars + 3) // 4
            c_completion_est += est_out_a
        else:
            unresolved = [f for f in extraction.unresolved_fields() if f in gap_allowed]
            if unresolved:
                context, candidates = build_context(extraction, unresolved)
                gap_usr = build_user_prompt(unresolved, context, candidates)
                c_calls += 1
                c_prompt_tokens += (len(GAPFILL_SYSTEM_PROMPT) + len(gap_usr) + 250) // 4
                c_completion_est += 120

    return {
        "documents": len(documents),
        "A_calls": a_calls,
        "A_prompt_tokens_est": a_prompt_tokens,
        "A_completion_tokens_est": a_completion_est,
        "A_total_tokens_est": a_prompt_tokens + a_completion_est,
        "B_calls": 0,
        "B_total_tokens_est": 0,
        "C_calls": c_calls,
        "C_prompt_tokens_est": c_prompt_tokens,
        "C_completion_tokens_est": c_completion_est,
        "C_total_tokens_est": c_prompt_tokens + c_completion_est,
        "combined_total_tokens_est": a_prompt_tokens + a_completion_est + c_prompt_tokens + c_completion_est,
    }


def summarize_approach(
    approach: ApproachId,
    results: list[DocumentRunResult],
) -> ApproachSummary:
    """Compute aggregate safety, accuracy, token, and latency metrics."""
    docs_count = len(results)
    total_fields = sum(r.total_fields for r in results)
    correct = sum(r.correct_fields for r in results)
    silent_errors = sum(r.silent_error_fields for r in results)
    missing = sum(r.missing_fields for r in results)
    manual_review = sum(r.manual_review_fields for r in results)

    latencies = [r.latency_seconds for r in results]
    total_tokens = sum(r.total_tokens for r in results)
    total_calls = sum(r.llm_calls for r in results)
    docs_with_llm = sum(1 for r in results if r.llm_calls > 0)

    return ApproachSummary(
        approach=approach,
        documents_count=docs_count,
        total_fields=total_fields,
        correct_fields=correct,
        silent_error_fields=silent_errors,
        missing_fields=missing,
        manual_review_fields=manual_review,
        accuracy=(correct / total_fields) if total_fields else 0.0,
        silent_error_rate=(silent_errors / total_fields) if total_fields else 0.0,
        abstention_rate=(manual_review / total_fields) if total_fields else 0.0,
        avg_tokens_per_doc=(total_tokens / docs_count) if docs_count else 0.0,
        total_tokens=total_tokens,
        avg_latency_seconds=statistics.mean(latencies) if latencies else 0.0,
        median_latency_seconds=statistics.median(latencies) if latencies else 0.0,
        max_latency_seconds=max(latencies) if latencies else 0.0,
        docs_with_manual_review=sum(1 for r in results if r.has_manual_review),
        total_llm_calls=total_calls,
        avg_llm_calls_per_doc=(total_calls / docs_count) if docs_count else 0.0,
        pct_docs_needing_llm=(docs_with_llm / docs_count * 100.0) if docs_count else 0.0,
        provider_failures=sum(1 for r in results if r.provider_failed),
    )


def _print_approach_report(
    title: str,
    documents: list[BenchmarkDocument],
    summary: ApproachSummary,
    results: list[DocumentRunResult],
) -> None:
    print(title)
    print(f"Documents evaluated: {len(documents)} | Total required fields: {summary.total_fields}")
    print("Required Commercial Invoice fields:")
    print(f"  Header ({len(INVOICE_HEADER_FIELDS)}): {', '.join(INVOICE_HEADER_FIELDS)}")
    print(f"  Line item ({len(INVOICE_ITEM_FIELDS)}): {', '.join(INVOICE_ITEM_FIELDS)}")
    print("Required Packing List fields:")
    print(f"  Header ({len(PACKING_HEADER_FIELDS)}): {', '.join(PACKING_HEADER_FIELDS)}")
    print(f"  Item ({len(PACKING_ITEM_FIELDS)}): {', '.join(PACKING_ITEM_FIELDS)}")
    print()
    hybrid_extra = (
        f", llm_calls/doc={summary.avg_llm_calls_per_doc:.2f} "
        f"(docs_needing_llm={summary.pct_docs_needing_llm:.1f}%)"
        if summary.approach == "C_hybrid"
        else ""
    )
    print(
        f"Approach {summary.approach}: "
        f"accuracy={summary.accuracy * 100:.2f}% ({summary.correct_fields}/{summary.total_fields}), "
        f"silent_error={summary.silent_error_rate * 100:.2f}% ({summary.silent_error_fields}), "
        f"abstention={summary.abstention_rate * 100:.2f}% ({summary.manual_review_fields}), "
        f"missing={summary.missing_fields}, "
        f"tokens/doc={summary.avg_tokens_per_doc:.1f} (total={summary.total_tokens}), "
        f"latency avg/med/max={summary.avg_latency_seconds:.4f}s/"
        f"{summary.median_latency_seconds:.4f}s/{summary.max_latency_seconds:.4f}s, "
        f"docs_with_manual_review={summary.docs_with_manual_review}/{summary.documents_count}"
        f"{hybrid_extra}, provider_failures={summary.provider_failures}"
    )
    print()
    print("Per-document breakdown:")
    for res in results:
        print(
            f"  - {res.document_id}: {res.correct_fields}/{res.total_fields} correct, "
            f"silent_err={res.silent_error_fields}, manual_review={res.manual_review_fields}, "
            f"missing={res.missing_fields}, llm_calls={res.llm_calls}, "
            f"tokens={res.total_tokens}, latency={res.latency_seconds:.4f}s"
        )

    per_field: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for res in results:
        for score in res.field_scores:
            key = f"{res.document_type}.{score.field_name}"
            per_field[key]["total"] += 1
            per_field[key][score.status] += 1

    print()
    print("Per-field breakdown (where status != 100% correct):")
    for field_key in sorted(per_field):
        counts = per_field[field_key]
        if counts["correct"] == counts["total"]:
            continue
        print(
            f"  - {field_key}: correct={counts['correct']}/{counts['total']}, "
            f"silent_err={counts['silent_error']}, manual_review={counts['manual_review']}, "
            f"missing={counts['missing']}"
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare LLM-only (A), Deterministic-only (B), and Hybrid (C) extraction on synthetic_factory bundles."
    )
    parser.add_argument(
        "--variant",
        choices=("text", "scanned", "both"),
        default="text",
        help="PDF variant to evaluate: 'text' (digital PDFs), 'scanned' (rasterized PDFs via Tesseract OCR), or 'both'.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of documents to evaluate (e.g., --limit 3).",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Optional substring filter on scenario folder name (e.g., clean_cotton_tshirts).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run zero-token Approach B (Deterministic-only) only without calling Groq.",
    )
    parser.add_argument(
        "--estimate-tokens",
        action="store_true",
        help="Estimate Groq API calls and token usage before running live (0 tokens spent).",
    )
    parser.add_argument(
        "--verify-offline",
        action="store_true",
        help="Exercise Approaches A, B, and C end-to-end against a deterministic offline stub (0 real Groq tokens).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass reading disk cache in --cache-dir (still caches new responses unless disabled).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="Directory for cached raw LLM responses and OCR page results.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.0,
        help="Optional delay in seconds between consecutive uncached live Groq calls (helps stay under free-tier TPM limits).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Markdown report output path (written when --write-report is passed).",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Write the measured Markdown report to --output after a live benchmark run.",
    )
    return parser


def _top_failure_cases(results: list[DocumentRunResult], *, limit: int = 5) -> list[str]:
    """Collect up to ``limit`` top concrete failures (silent errors first, then provider/abstain)."""
    severity = {"silent_error": 0, "missing": 1, "manual_review": 2}
    items: list[tuple[int, str]] = []
    for res in results:
        if res.provider_failed and res.error_message:
            items.append(
                (
                    -1,
                    f"`{res.document_id}`: provider call failed — `{res.error_message[:140]}`",
                )
            )
        for score in res.field_scores:
            if score.status == "correct":
                continue
            rank = severity[score.status]
            items.append(
                (
                    rank,
                    f"`{res.document_id}` -> `{score.path}` (`{score.status}`): "
                    f"expected `{score.expected!r}`, got `{score.extracted!r}`",
                )
            )
    items.sort(key=lambda pair: pair[0])
    seen: list[str] = []
    for _, line in items:
        if line not in seen:
            seen.append(line)
        if len(seen) >= limit:
            break
    return seen


_SUMMARY_TABLE_HEADER = (
    "| Approach | Field Accuracy | Silent Error Rate | Abstention (`MANUAL_REVIEW`) | "
    "Unflagged Missing | Avg Tokens / Doc (Total) | Latency Avg / Med / Max | "
    "Docs w/ `MANUAL_REVIEW` | Hybrid LLM Calls / Doc (% Docs Needing LLM) | Provider Failures |"
)
_SUMMARY_TABLE_ALIGN = "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"


def _resolve_report_variant(
    documents: list[BenchmarkDocument],
    explicit_variant: str | None,
) -> Literal["text", "scanned", "both"]:
    if explicit_variant == "both":
        return "both"
    if explicit_variant == "scanned":
        return "scanned"
    if explicit_variant == "text":
        return "text"
    variants_present = {doc.variant for doc in documents}
    if variants_present == {"text", "scanned"}:
        return "both"
    if variants_present == {"scanned"}:
        return "scanned"
    return "text"


def render_benchmark_markdown(
    *,
    documents: list[BenchmarkDocument],
    summaries: dict[ApproachId, ApproachSummary],
    results_by_approach: dict[ApproachId, list[DocumentRunResult]],
    model_name: str,
    variant: str | None = None,
) -> str:
    """Render an auditable Markdown report purely from measured results."""
    ordered: tuple[tuple[ApproachId, str], ...] = (
        ("A_llm_only", "A) LLM-only (`single-shot Groq`)"),
        ("B_deterministic_only", "B) Deterministic-only (`regex + coords`)"),
        ("C_hybrid", "C) CACE Hybrid (`regex/coords + 1-call gapfill`)"),
    )
    resolved_variant = _resolve_report_variant(documents, variant)
    text_count = sum(1 for d in documents if d.variant == "text")
    scanned_count = sum(1 for d in documents if d.variant == "scanned")
    if resolved_variant == "scanned":
        dataset_label = f"{len(documents)} scanned/OCR PDFs"
    elif resolved_variant == "both":
        dataset_label = (
            f"{len(documents)} PDFs ({text_count} digital + {scanned_count} scanned/OCR)"
        )
    else:
        dataset_label = f"{len(documents)} digital PDFs"

    sum_a = summaries["A_llm_only"]
    sum_b = summaries["B_deterministic_only"]
    sum_c = summaries["C_hybrid"]

    # Build unthrottled subset (documents where Approach A succeeded without provider errors)
    unthrottled_ids = {
        res.document_id
        for res in results_by_approach["A_llm_only"]
        if not res.provider_failed
    }
    has_partial_429 = 0 < len(unthrottled_ids) < len(documents)

    def _format_summary_rows(target_summaries: dict[ApproachId, ApproachSummary]) -> list[str]:
        rows: list[str] = []
        for aid, label in ordered:
            s = target_summaries[aid]
            hybrid_col = (
                f"{s.avg_llm_calls_per_doc:.2f} ({s.pct_docs_needing_llm:.1f}%)"
                if aid == "C_hybrid"
                else "—"
            )
            rows.append(
                f"| {label} | "
                f"**{s.accuracy * 100:.2f}%** ({s.correct_fields}/{s.total_fields}) | "
                f"{s.silent_error_rate * 100:.2f}% ({s.silent_error_fields}) | "
                f"{s.abstention_rate * 100:.2f}% ({s.manual_review_fields}) | "
                f"{s.missing_fields} | "
                f"{s.avg_tokens_per_doc:.1f} ({s.total_tokens:,}) | "
                f"{s.avg_latency_seconds:.3f}s / {s.median_latency_seconds:.3f}s / {s.max_latency_seconds:.3f}s | "
                f"{s.docs_with_manual_review}/{s.documents_count} | "
                f"{hybrid_col} | {s.provider_failures} |"
            )
        return rows

    lines: list[str] = [
        f"# CACE Extraction Benchmark (`--variant {resolved_variant}`)",
        "",
        f"Measured comparison across **{dataset_label}** "
        f"({len({d.scenario_id for d in documents})} scenarios, **{sum_c.total_fields} required fields**) "
        f"using Groq model `{model_name}` (`temperature=0`, `max_retries=0`).",
        "",
        "## 1. Results Summary (Full Batch)",
        "",
        _SUMMARY_TABLE_HEADER,
        _SUMMARY_TABLE_ALIGN,
        *_format_summary_rows(summaries),
    ]

    if has_partial_429:
        sub_summaries = {
            aid: summarize_approach(
                aid,
                [r for r in results_by_approach[aid] if r.document_id in unthrottled_ids],
            )
            for aid, _ in ordered
        }
        sub_a = sub_summaries["A_llm_only"]
        lines.extend(
            [
                "",
                f"### Non-Rate-Limited Subset ({len(unthrottled_ids)} Documents / {sub_a.total_fields} Fields Where Approach A Cleared TPM Quota)",
                "",
                _SUMMARY_TABLE_HEADER,
                _SUMMARY_TABLE_ALIGN,
                *_format_summary_rows(sub_summaries),
            ]
        )

    if resolved_variant == "both":
        lines.extend(["", "### Breakdown by Variant (`text` vs. `scanned`)"])
        for sub_variant, sub_title in (
            ("text", "Digital PDFs (`text`)"),
            ("scanned", "Scanned/OCR PDFs (`scanned`)"),
        ):
            variant_docs = [d for d in documents if d.variant == sub_variant]
            if not variant_docs:
                continue
            variant_summaries = {
                aid: summarize_approach(
                    aid,
                    [r for r in results_by_approach[aid] if r.variant == sub_variant],
                )
                for aid, _ in ordered
            }
            sub_fields = variant_summaries["C_hybrid"].total_fields
            lines.extend(
                [
                    "",
                    f"#### {sub_title} ({len(variant_docs)} Documents / {sub_fields} Fields)",
                    "",
                    _SUMMARY_TABLE_HEADER,
                    _SUMMARY_TABLE_ALIGN,
                    *_format_summary_rows(variant_summaries),
                ]
            )

    # Honest data-driven two-paragraph interpretation.
    token_savings = (
        f"{(1 - (sum_c.total_tokens / sum_a.total_tokens)) * 100:.1f}%"
        if sum_a.total_tokens > 0
        else "N/A"
    )
    hybrid_tradeoffs: list[str] = []
    if sum_c.accuracy < sum_a.accuracy:
        hybrid_tradeoffs.append(
            f"Approach A achieved higher raw accuracy ({sum_a.accuracy * 100:.2f}% vs. {sum_c.accuracy * 100:.2f}%)"
        )
    if sum_c.silent_error_rate > sum_a.silent_error_rate:
        hybrid_tradeoffs.append(
            f"Approach C had a higher silent-error rate ({sum_c.silent_error_rate * 100:.2f}% vs. {sum_a.silent_error_rate * 100:.2f}%)"
        )
    if sum_c.avg_latency_seconds > sum_b.avg_latency_seconds:
        hybrid_tradeoffs.append(
            f"Approach B was faster on average ({sum_b.avg_latency_seconds:.3f}s vs. {sum_c.avg_latency_seconds:.3f}s)"
        )
    if sum_c.accuracy == sum_b.accuracy:
        scope_phrase = (
            "on these digital PDFs"
            if resolved_variant == "text"
            else ("on these scanned/OCR PDFs" if resolved_variant == "scanned" else "across this batch")
        )
        hybrid_tradeoffs.append(
            f"{scope_phrase} Approach C matched Approach B on accuracy ({sum_c.accuracy * 100:.2f}%) without improving it"
        )
    tradeoff_note = (
        " Honest tradeoffs observed: " + "; ".join(hybrid_tradeoffs) + "."
        if hybrid_tradeoffs
        else ""
    )

    scanned_ocr_latencies = [
        r.ocr_latency_seconds
        for r in results_by_approach["B_deterministic_only"]
        if r.variant == "scanned"
    ]
    ocr_note = (
        f" Tesseract OCR preprocessing added **{statistics.mean(scanned_ocr_latencies):.3f}s/doc** on average "
        f"across the {len(scanned_ocr_latencies)} scanned PDF(s) (with PyMuPDF word coordinates absent on raster pages, "
        f"triggering single-shot table fallback when deterministic table reconstruction sees empty coordinates)."
        if scanned_ocr_latencies
        else ""
    )

    c_docs_zero_llm = sum(1 for r in results_by_approach["C_hybrid"] if r.llm_calls == 0)

    if resolved_variant == "scanned":
        limitation_variant = (
            "- **Scanned/OCR PDFs only (`--variant scanned`)**: Rasterized pages lack PyMuPDF word coordinates "
            "(`words=[]`), so coordinate-based table reconstruction falls back to OCR regex or one full-document "
            "LLM call; digital PDFs were not included in this run."
        )
    elif resolved_variant == "both":
        limitation_variant = (
            "- **Combined digital + scanned batch (`--variant both`)**: Scanned/OCR pages lack PyMuPDF word coordinates "
            "(`words=[]`) and depend on Tesseract 300 DPI OCR output quality before regex/LLM extraction."
        )
    else:
        limitation_variant = (
            "- **Digital PDFs only (`--variant text`)**: Scanned/Tesseract OCR PDFs (`*_scanned.pdf`) were not "
            "included in this run (pass `--variant scanned` or `--variant both`)."
        )

    lines.extend(
        [
            "",
            "## 2. Plain-English Interpretation & Tradeoffs",
            "",
            (
                f"Across all {dataset_label} ({sum_c.total_fields} required fields), "
                f"Approach B (Deterministic-only) ran at **{sum_b.avg_latency_seconds:.3f}s/doc** and **0 tokens**, "
                f"achieving **{sum_b.accuracy * 100:.2f}%** accuracy with **{sum_b.manual_review_fields}** fail-closed "
                f"`MANUAL_REVIEW` abstentions where multi-line weights or package counts were not printed in table columns. "
                f"Approach C (CACE Hybrid) resolved **{c_docs_zero_llm}/{sum_c.documents_count}** documents locally at 0 tokens "
                f"and escalated to Groq on only **{sum_c.pct_docs_needing_llm:.1f}%** of documents "
                f"({sum_c.total_llm_calls} call(s), **{sum_c.total_tokens:,}** total tokens — **{token_savings}** fewer total tokens than Approach A's **{sum_a.total_tokens:,}** tokens across {sum_a.total_llm_calls} calls)."
                f"{ocr_note}"
            ),
            "",
            (
                f"Comparing safety and reliability, sending every document straight to Groq (Approach A) averaged "
                f"**{sum_a.avg_latency_seconds:.3f}s/doc** (median {sum_a.median_latency_seconds:.3f}s, max {sum_a.max_latency_seconds:.3f}s) "
                f"and reached **{sum_a.accuracy * 100:.2f}%** accuracy with **{sum_a.silent_error_rate * 100:.2f}%** silent errors and "
                f"**{sum_a.manual_review_fields}** `MANUAL_REVIEW` abstentions ({sum_a.provider_failures} provider failures). "
                f"Because Hybrid only calls the LLM when deterministic regex/coordinate parsing leaves header gaps (and never sends per-line table weights to free-form guessing), "
                f"it minimizes token cost and latency while keeping safety boundaries deterministic.{tradeoff_note}"
            ),
            "",
            "## 3. Shared Required Fields & Fairness Rules",
            "",
            "Every approach evaluated the exact same PDFs, used the same Pydantic candidate/materialized schemas (`MultiLineInvoiceCandidates`, `MultiLinePackingListCandidates`), passed through the same provenance & confidence gate (`>= 0.80`), and was scored by `extraction_accuracy._compare_value`:",
            "",
            f"- **Commercial Invoice Header ({len(INVOICE_HEADER_FIELDS)})**: `{', '.join(INVOICE_HEADER_FIELDS)}`",
            f"- **Commercial Invoice Line Item ({len(INVOICE_ITEM_FIELDS)})**: `{', '.join(INVOICE_ITEM_FIELDS)}`",
            f"- **Packing List Header ({len(PACKING_HEADER_FIELDS)})**: `{', '.join(PACKING_HEADER_FIELDS)}`",
            f"- **Packing List Item ({len(PACKING_ITEM_FIELDS)})**: `{', '.join(PACKING_ITEM_FIELDS)}`",
            "",
            "## 4. Per-Field Breakdown (Approach C Hybrid)",
            "",
            "| Document.Field | Correct | Silent Errors | `MANUAL_REVIEW` | Missing |",
            "| :--- | :---: | :---: | :---: | :---: |",
        ]
    )

    per_field_c: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for res in results_by_approach["C_hybrid"]:
        for score in res.field_scores:
            key = f"{res.document_type}.{score.field_name}"
            per_field_c[key]["total"] += 1
            per_field_c[key][score.status] += 1
    for field_key in sorted(per_field_c):
        counts = per_field_c[field_key]
        if counts["correct"] == counts["total"]:
            continue
        lines.append(
            f"| `{field_key}` | {counts['correct']}/{counts['total']} | "
            f"{counts['silent_error']} | {counts['manual_review']} | {counts['missing']} |"
        )

    lines.extend(["", "## 5. Error Analysis (Top 5 Failure Cases per Approach)", ""])

    for aid, label in ordered:
        lines.append(f"### {label}")
        cases = _top_failure_cases(results_by_approach[aid], limit=5)
        if not cases:
            lines.append("- No failures recorded.")
        else:
            for item in cases:
                lines.append(f"- {item}")
        lines.append("")

    lines.extend(
        [
            "## 6. Limitations",
            "",
            f"- **Synthetic dataset**: All {len(documents)} evaluated documents come from `synthetic_factory/` fixtures rather than noisy third-party exporter scans.",
            limitation_variant,
            f"- **Single model snapshot**: All live LLM runs used Groq `{model_name}` (`temperature=0`, `max_retries=0`); even at `temperature=0`, hosted LLM decoding can show minor run-to-run variation.",
            "- **Strict string matching on punctuation**: `extraction_accuracy._compare_value` uses `.strip().casefold()` equality without punctuation stripping (so regex normalizer trimming `'Ltd.'` -> `'Ltd'` is counted honestly as a `silent_error`).",
            "",
        ]
    )
    return "\n".join(lines)


def _print_token_estimate(estimate: dict[str, Any]) -> None:
    print(f"Pre-flight Groq Token Estimate ({estimate['documents']} documents, 0 tokens spent now):")
    print(
        f"  - Approach A (LLM-only):     {estimate['A_calls']} calls, "
        f"~{estimate['A_prompt_tokens_est']:,} prompt + ~{estimate['A_completion_tokens_est']:,} completion "
        f"= ~{estimate['A_total_tokens_est']:,} tokens"
    )
    print("  - Approach B (Deterministic): 0 calls, 0 tokens")
    print(
        f"  - Approach C (Hybrid):       {estimate['C_calls']} calls, "
        f"~{estimate['C_prompt_tokens_est']:,} prompt + ~{estimate['C_completion_tokens_est']:,} completion "
        f"= ~{estimate['C_total_tokens_est']:,} tokens"
    )
    print(
        f"  - Combined Live Total Estimate: ~{estimate['combined_total_tokens_est']:,} tokens "
        "(Groq gpt-oss-20b reasoning may raise completion tokens above estimate)."
    )


def _load_backend_dotenv() -> None:
    """Populate missing env vars from ``backend/.env`` when invoked outside backend/."""
    env_path = BACKEND_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)
    # Imported services (e.g., document_service) call get_settings() at module
    # import time, so clear the lru_cache after loading backend/.env.
    get_settings.cache_clear()


def _preload_bundles(
    documents: list[BenchmarkDocument],
    *,
    cache_dir: Path | None,
    use_cache: bool,
) -> dict[str, LoadedBundle]:
    """Load/OCR each document once so Approaches A, B, and C share identical text."""
    return {
        doc.document_id: _load_pdf_bundle(
            doc,
            cache_dir=cache_dir,
            use_cache=use_cache,
        )
        for doc in documents
    }


def main(argv: list[str] | None = None) -> int:
    _load_backend_dotenv()
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    if args.delay_seconds < 0:
        parser.error("--delay-seconds must be non-negative")

    try:
        documents = load_benchmark_documents(
            factory_root=FACTORY_ROOT,
            variant=args.variant,
            limit=args.limit,
            scenario_filter=args.scenario,
        )
    except (NotImplementedError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    is_live_run = not (args.estimate_tokens or args.dry_run or args.verify_offline)
    if is_live_run:
        settings = get_settings()
        if settings.groq_api_key is None or not settings.groq_api_key.get_secret_value().strip():
            print(
                "ERROR: GROQ_API_KEY is not configured in environment or backend/.env.",
                file=sys.stderr,
            )
            return 1

    use_cache = not args.no_cache
    try:
        loaded_bundles = _preload_bundles(
            documents,
            cache_dir=args.cache_dir,
            use_cache=use_cache,
        )
    except OcrConfigurationError as exc:
        print(
            f"ERROR: {exc}\n"
            "Install Tesseract OCR (for example: sudo apt install tesseract-ocr) "
            "or set CUSTOMS_OCR_EXECUTABLE in backend/.env before running scanned benchmarks.",
            file=sys.stderr,
        )
        return 2

    if args.estimate_tokens:
        _print_token_estimate(
            estimate_token_budget(documents, loaded_bundles=loaded_bundles)
        )
        if not args.dry_run and not args.verify_offline:
            return 0
        print()

    if args.dry_run:
        results_b = [
            run_approach_b(doc, loaded_bundle=loaded_bundles[doc.document_id])
            for doc in documents
        ]
        summary_b = summarize_approach("B_deterministic_only", results_b)
        _print_approach_report(
            "CACE Extraction Benchmark — Approach B Dry-Run (Deterministic-Only)",
            documents,
            summary_b,
            results_b,
        )
        return 0

    if args.verify_offline:
        results_a = [
            run_approach_a(
                doc,
                client_factory=_make_offline_stub_client_factory(doc),
                loaded_bundle=loaded_bundles[doc.document_id],
            )
            for doc in documents
        ]
        results_b = [
            run_approach_b(doc, loaded_bundle=loaded_bundles[doc.document_id])
            for doc in documents
        ]
        results_c = [
            run_approach_c(
                doc,
                client_factory=_make_offline_stub_client_factory(doc),
                loaded_bundle=loaded_bundles[doc.document_id],
            )
            for doc in documents
        ]
        for label, aid, res_list in (
            ("Offline Stub Check — Approach A (LLM-only)", "A_llm_only", results_a),
            ("Offline Stub Check — Approach B (Deterministic-Only)", "B_deterministic_only", results_b),
            ("Offline Stub Check — Approach C (Hybrid)", "C_hybrid", results_c),
        ):
            _print_approach_report(
                label,
                documents,
                summarize_approach(cast(ApproachId, aid), res_list),
                res_list,
            )
            print()
        return 0

    _print_token_estimate(
        estimate_token_budget(documents, loaded_bundles=loaded_bundles)
    )
    print()
    pacer = _LiveCallPacer(args.delay_seconds)
    results_a = [
        run_approach_a(
            doc,
            cache_dir=args.cache_dir,
            use_cache=use_cache,
            loaded_bundle=loaded_bundles[doc.document_id],
            pacer=pacer,
        )
        for doc in documents
    ]
    results_b = [
        run_approach_b(doc, loaded_bundle=loaded_bundles[doc.document_id])
        for doc in documents
    ]
    results_c = [
        run_approach_c(
            doc,
            cache_dir=args.cache_dir,
            use_cache=use_cache,
            loaded_bundle=loaded_bundles[doc.document_id],
            pacer=pacer,
        )
        for doc in documents
    ]
    summaries: dict[ApproachId, ApproachSummary] = {}
    for label, aid, res_list in (
        ("Live Benchmark — Approach A (LLM-only)", "A_llm_only", results_a),
        ("Live Benchmark — Approach B (Deterministic-Only)", "B_deterministic_only", results_b),
        ("Live Benchmark — Approach C (Hybrid)", "C_hybrid", results_c),
    ):
        typed_id = cast(ApproachId, aid)
        summary = summarize_approach(typed_id, res_list)
        summaries[typed_id] = summary
        _print_approach_report(label, documents, summary, res_list)
        print()

    if args.write_report:
        markdown = render_benchmark_markdown(
            documents=documents,
            summaries=summaries,
            results_by_approach={
                "A_llm_only": results_a,
                "B_deterministic_only": results_b,
                "C_hybrid": results_c,
            },
            model_name=get_settings().groq_model,
            variant=args.variant,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
        print(f"Wrote Markdown benchmark report to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
