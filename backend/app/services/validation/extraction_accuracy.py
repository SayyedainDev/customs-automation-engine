"""Reusable comparison of expected vs extracted shipment structured values.

This module is deliberately independent of how the extracted values were
produced (live Groq extraction on real PDFs, or an injected deterministic
extractor on synthetic fixtures). It compares a hand-labelled ground truth
against a ``MultiLineShipmentResponse`` and reports per-field outcomes plus
aggregate accuracy, so the same harness works for real and synthetic runs.

A field is:
- ``correct``       extracted value equals the expected value;
- ``incorrect``     extracted a non-null value that differs from expected;
- ``missing``       expected a value but extracted null without flagging review;
- ``manual_review`` the field was withheld and routed to manual review.

``manual_review`` is treated as a safe outcome (the system declined to trust a
value), never as a correct extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.schemas.multi_line_extraction import (
    InvoiceLineItem,
    MultiLineShipmentResponse,
)
from app.schemas.shipment_extraction import FieldValidationStatus


_ITEM_FIELDS = (
    "product_name",
    "pct_code",
    "quantity",
    "unit_price",
    "line_total",
    "net_weight",
    "gross_weight",
)


@dataclass(frozen=True)
class ExpectedItem:
    product_name: str | None
    pct_code: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None
    net_weight: Decimal | None
    gross_weight: Decimal | None


@dataclass(frozen=True)
class FieldOutcome:
    location: str
    field_name: str
    expected: object
    extracted: object
    status: str  # correct | incorrect | missing | manual_review


@dataclass
class AccuracyMetrics:
    total: int = 0
    correct: int = 0
    incorrect: int = 0
    missing: int = 0
    manual_review: int = 0

    def add(self, status: str) -> None:
        self.total += 1
        setattr(self, status, getattr(self, status) + 1)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


def _normalize_pct(value: object) -> object:
    if isinstance(value, str):
        return value.replace(".", "").strip()
    return value


def _compare_value(field_name: str, expected: object, extracted: object) -> str:
    left = _normalize_pct(expected) if field_name == "pct_code" else expected
    right = _normalize_pct(extracted) if field_name == "pct_code" else extracted
    if isinstance(left, str) and isinstance(right, str):
        return "correct" if left.strip().casefold() == right.strip().casefold() else "incorrect"
    return "correct" if left == right else "incorrect"


def compare_invoice_item(
    location: str,
    expected: ExpectedItem,
    actual: InvoiceLineItem | None,
) -> list[FieldOutcome]:
    outcomes: list[FieldOutcome] = []
    for field_name in _ITEM_FIELDS:
        expected_value = getattr(expected, field_name)
        if actual is None:
            outcomes.append(
                FieldOutcome(location, field_name, expected_value, None, "missing")
            )
            continue
        extracted_field = getattr(actual, field_name)
        extracted_value = extracted_field.value
        if extracted_field.validation_status == FieldValidationStatus.MANUAL_REVIEW:
            status = "manual_review"
        elif extracted_value is None:
            status = "missing" if expected_value is not None else "correct"
        else:
            status = _compare_value(field_name, expected_value, extracted_value)
        outcomes.append(
            FieldOutcome(
                location,
                field_name,
                expected_value,
                extracted_value,
                status,
            )
        )
    return outcomes


@dataclass
class ScenarioComparison:
    scenario_id: str
    field_outcomes: list[FieldOutcome] = field(default_factory=list)
    pct_metrics: AccuracyMetrics = field(default_factory=AccuracyMetrics)
    field_metrics: AccuracyMetrics = field(default_factory=AccuracyMetrics)
    matching_correct: int = 0
    matching_total: int = 0
    mismatch_expected: bool = False
    mismatch_detected: bool = False
    expected_overall_status: str = ""
    actual_overall_status: str = ""
    false_legal_pass: bool = False

    @property
    def matching_accuracy(self) -> float:
        return self.matching_correct / self.matching_total if self.matching_total else 0.0


def compare_shipment(
    *,
    scenario_id: str,
    expected_items: list[ExpectedItem],
    expected_match_statuses: list[str],
    expected_overall_status: str,
    mismatch_expected: bool,
    response: MultiLineShipmentResponse,
) -> ScenarioComparison:
    """Compare one scenario's ground truth against a pipeline response."""

    comparison = ScenarioComparison(
        scenario_id=scenario_id,
        expected_overall_status=expected_overall_status,
        actual_overall_status=response.overall_status.value,
        mismatch_expected=mismatch_expected,
    )

    # Field-level accuracy over the extracted invoice line items.
    for index, expected_item in enumerate(expected_items):
        actual_item = (
            response.invoice.line_items[index]
            if index < len(response.invoice.line_items)
            else None
        )
        outcomes = compare_invoice_item(
            f"{scenario_id}.invoice_item[{index + 1}]",
            expected_item,
            actual_item,
        )
        for outcome in outcomes:
            comparison.field_outcomes.append(outcome)
            comparison.field_metrics.add(outcome.status)
            if outcome.field_name == "pct_code":
                comparison.pct_metrics.add(outcome.status)

    # Item-matching accuracy.
    actual_statuses = [item.match_status.value for item in response.items]
    comparison.matching_total = max(
        len(expected_match_statuses), len(actual_statuses)
    )
    for index, expected_status in enumerate(expected_match_statuses):
        if index < len(actual_statuses) and actual_statuses[index] == expected_status:
            comparison.matching_correct += 1

    # Mismatch detection: did any deterministic check fail as expected?
    any_failed = response.overall_status.value == "failed" or any(
        check.status.value == "failed"
        for item in response.items
        for check in item.item_checks
    ) or any(
        check.status.value == "failed" for check in response.shipment_level_checks
    )
    comparison.mismatch_detected = any_failed
    comparison.false_legal_pass = (
        expected_overall_status != "passed"
        and response.overall_status.value == "passed"
    )
    return comparison


def aggregate(comparisons: list[ScenarioComparison]) -> dict[str, object]:
    field_totals = AccuracyMetrics()
    pct_totals = AccuracyMetrics()
    matching_correct = 0
    matching_total = 0
    mismatch_correct = 0
    mismatch_total = 0
    false_legal_passes = 0
    for comparison in comparisons:
        for attr in ("total", "correct", "incorrect", "missing", "manual_review"):
            setattr(
                field_totals,
                attr,
                getattr(field_totals, attr) + getattr(comparison.field_metrics, attr),
            )
            setattr(
                pct_totals,
                attr,
                getattr(pct_totals, attr) + getattr(comparison.pct_metrics, attr),
            )
        matching_correct += comparison.matching_correct
        matching_total += comparison.matching_total
        if comparison.mismatch_expected:
            mismatch_total += 1
            if comparison.mismatch_detected:
                mismatch_correct += 1
        if comparison.false_legal_pass:
            false_legal_passes += 1
    return {
        "total_fields_tested": field_totals.total,
        "correctly_extracted_fields": field_totals.correct,
        "incorrect_fields": field_totals.incorrect,
        "missing_fields": field_totals.missing,
        "manual_review_fields": field_totals.manual_review,
        "field_extraction_accuracy": round(field_totals.accuracy, 4),
        "pct_code_accuracy": round(pct_totals.accuracy, 4),
        "item_matching_accuracy": round(
            matching_correct / matching_total if matching_total else 0.0, 4
        ),
        "mismatch_detection_accuracy": round(
            mismatch_correct / mismatch_total if mismatch_total else 0.0, 4
        ),
        "false_legal_passes": false_legal_passes,
    }
