"""Deterministic retrieval-query construction from a compliance check.

Reuses the existing regulatory query builder; no LLM decides the query.
"""

from __future__ import annotations

from typing import Any

from app.services.regulatory.query_builder import (
    ComplianceQueryInputs,
    build_compliance_query,
)


def query_for_check(check: dict[str, Any], extraction_result: dict[str, Any]) -> str:
    return build_compliance_query(
        ComplianceQueryInputs(
            check_id=str(check.get("check_id", "")),
            check_name=check.get("check_name"),
            pct_code=check.get("pct_code"),
            required_document=check.get("required_document"),
            sro_number=check.get("sro_number"),
            source_document=check.get("source_document"),
            message=check.get("message"),
        )
    )
