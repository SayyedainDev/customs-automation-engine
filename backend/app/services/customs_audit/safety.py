"""Prompt-injection detection and agent-output validation.

Two protections:
1. Detect prompt-injection style text inside untrusted document / evidence
   content so it can be logged and never acted upon (agents derive findings
   deterministically, so such text can never change an outcome; detection adds an
   audit note and defensive routing).
2. Validate structured agent output: an agent may never change the deterministic
   status, cite evidence that was not retrieved, or invent an SRO. Any violation
   forces manual review (fail closed).
"""

from __future__ import annotations

import re

from app.services.customs_audit.state import AuditorReport, BrokerReport

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(the\s+)?(above|previous|prior)", re.IGNORECASE),
    re.compile(r"mark\s+(this\s+)?(shipment|it)\s+(as\s+)?compliant", re.IGNORECASE),
    re.compile(r"\bmark\s+.*\b(passed|compliant)\b", re.IGNORECASE),
    re.compile(r"change\s+the\s+pct\s+code", re.IGNORECASE),
    re.compile(r"reveal\s+the\s+system\s+prompt", re.IGNORECASE),
    re.compile(r"skip\s+(the\s+)?human\s+review", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"call\s+(another\s+)?tool", re.IGNORECASE),
    re.compile(r"override\s+the\s+(compliance|deterministic)", re.IGNORECASE),
]

_SRO_IN_TEXT = re.compile(r"\b\d{2,4}\s*\(\s*[IiVvXx]+\s*\)\s*/\s*\d{4}\b")


def detect_injection(text: str | None) -> list[str]:
    """Return the injection phrases found in untrusted text (as data, not obeyed)."""
    if not text:
        return []
    found: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            found.append(match.group(0))
    return found


def scan_documents_for_injection(texts: list[str | None]) -> list[str]:
    hits: list[str] = []
    for text in texts:
        hits.extend(detect_injection(text))
    return hits


def _normalize_sro(value: str) -> str:
    return re.sub(r"[^0-9ivx]", "", value.casefold())


def validate_broker_report(
    report: BrokerReport,
    deterministic_status: str,
) -> list[str]:
    violations: list[str] = []
    if (
        report.observed_deterministic_status is not None
        and report.observed_deterministic_status != deterministic_status
    ):
        violations.append(
            "broker report reports a deterministic status that differs from the engine"
        )
    return violations


def validate_auditor_report(
    report: AuditorReport,
    deterministic_status: str,
    retrieved_sro_numbers: set[str],
) -> list[str]:
    violations: list[str] = []
    if (
        report.observed_deterministic_status is not None
        and report.observed_deterministic_status != deterministic_status
    ):
        violations.append(
            "auditor report reports a deterministic status that differs from the engine"
        )
    # No SRO identifier may appear in audit notes unless it was retrieved.
    retrieved = {_normalize_sro(number) for number in retrieved_sro_numbers}
    for note in report.audit_notes + report.conflicting_evidence:
        for match in _SRO_IN_TEXT.finditer(note):
            if _normalize_sro(match.group(0)) not in retrieved:
                violations.append(
                    f"auditor cited unsupported SRO '{match.group(0)}' not in retrieved evidence"
                )
    return violations
