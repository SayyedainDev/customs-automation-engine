"""Validated Pydantic models for the Phase-2 executable compliance rules.

An executable rule is a flat, self-describing legal requirement with complete
provenance. The deterministic engine evaluates these records generically, so a
new textile rule can be expressed as data instead of a new Python branch. The
LLM never produces or edits these records and never decides their outcome.

Fail-closed policy encoded here:
- Only ``verified`` and ``partially_verified`` rules may drive a positive
  (passed / not_applicable) outcome; every other validation status forces
  ``manual_review``.
- ``result_builder.build_result`` still applies its own provenance gate on top,
  so an otherwise-passing rule with incomplete legal provenance is downgraded to
  ``manual_review`` regardless.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ExecutableCheckType(str, Enum):
    REQUIRED_DOCUMENT = "required_document"
    CONDITIONAL_DOCUMENT = "conditional_document"
    DESTINATION_DOCUMENT = "destination_document"
    SHIPMENT_DEADLINE = "shipment_deadline"
    REQUIREMENT_STATUS = "requirement_status"
    EXPORT_STATUS_INFORMATIONAL = "export_status_informational"


class RuleValidationStatus(str, Enum):
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    UNVERIFIED = "unverified"
    INACCESSIBLE = "inaccessible"
    CONFLICTING = "conflicting"
    MANUAL_REVIEW = "manual_review"


# Statuses that may support a positive (passed / not_applicable) decision.
POSITIVE_DECISION_STATUSES = frozenset(
    {RuleValidationStatus.VERIFIED, RuleValidationStatus.PARTIALLY_VERIFIED}
)


class ExecutableRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    rule_id: str
    rule_name: str
    applies_to_pct_codes: list[str]
    export_status: str | None = None
    check_type: ExecutableCheckType
    trigger: str | None = None
    required_document: str | None = None
    required_approval: str | None = None
    responsible_agency: str | None = None
    destination_country: str | None = None
    numeric_value: str | None = None
    deadline_days: int | None = Field(default=None, ge=0)
    deadline_anchor: str | None = None

    # Provenance.
    source_document: str | None = None
    sro_number: str | None = None
    source_url: str | None = None
    source_page: int | None = Field(default=None, ge=1)
    source_locator: str | None = None
    issuing_authority: str | None = None
    issue_date: date | None = None
    effective_date: date | None = None
    legal_cutoff_date: date | None = None
    retrieval_date: date | None = None
    validation_status: RuleValidationStatus
    validation_note: str

    @property
    def supports_positive_decision(self) -> bool:
        return self.validation_status in POSITIVE_DECISION_STATUSES


class ExecutableRuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    generated_from: list[str]
    legal_cutoff_date: date
    retrieval_date: date
    rules: list[ExecutableRule]

    def rules_for(self, pct_code: str) -> list[ExecutableRule]:
        return [rule for rule in self.rules if pct_code in rule.applies_to_pct_codes]
