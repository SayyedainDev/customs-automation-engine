"""Model Context Protocol (MCP) server for the Enterprise Customs Automation Engine (CACE).

Exposes CACE's authoritative deterministic tools to MCP-compatible AI clients:
1. ``lookup_pakistan_tariff``: Exact/prefix HS/PCT code lookup from ``tariff_lines``.
2. ``calculate_pakistan_customs_duties``: Exact Decimal 2026-27 cascading duty & tax calculator.
3. ``get_textile_export_checklist``: Deterministic textile export document checklist.
4. ``search_pakistan_customs_regulations``: Hybrid BM25 + BGE-Small + CrossEncoder regulatory search.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from app.core.database import get_db_session
from app.main import app
from app.services.compliance.duty_calculator import (
    calculate_cascading_customs_taxes,
    format_cascading_steps,
)
from app.services.customs_chat.knowledge import checklist_tool, tariff_lookup
from app.services.regulatory.retrieval import search_regulatory_evidence
from sqlalchemy.orm import Session


@contextmanager
def _db_session():
    provider = app.dependency_overrides.get(get_db_session, get_db_session)
    gen = provider()
    try:
        db: Session = next(gen)
        yield db
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
except ImportError:

    class FastMCP:  # type: ignore[no-redef]
        """Lightweight in-process FastMCP fallback when ``mcp`` SDK is not installed."""

        def __init__(self, name: str):
            self.name = name
            self.tools: dict[str, Callable[..., Any]] = {}

        def tool(self, name: str | None = None, description: str | None = None):
            def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                tool_name = name or fn.__name__
                self.tools[tool_name] = fn
                return fn

            return decorator

        def run(self) -> None:
            raise RuntimeError(
                "Install the 'mcp' package (`pip install mcp`) to run the stdio/SSE MCP transport."
            )


mcp = FastMCP("cace-pakistan-customs-engine")


@mcp.tool(
    name="lookup_pakistan_tariff",
    description="Look up an 8-digit Pakistan Customs Tariff (PCT/HS) code or 4/6-digit prefix.",
)
def lookup_pakistan_tariff(hs_code: str) -> dict[str, Any]:
    with _db_session() as db:
        result = tariff_lookup.lookup_tariff(db, hs_code)
    return {
        "query_code": result.query_code,
        "matched": result.matched,
        "exact": result.exact,
        "hs_code": result.hs_code,
        "description": result.description,
        "customs_duty_pct": result.customs_duty_pct,
        "tariff_year": result.tariff_year,
        "source_page": result.source_page,
        "needs_review": result.needs_review,
        "candidates": list(result.candidates),
        "cascading_tax_breakdown": result.cascading_tax_breakdown,
    }


@mcp.tool(
    name="calculate_pakistan_customs_duties",
    description="Calculate exact 2026-27 Pakistan Customs cascading duties and taxes (CD, ACD, RD, ST, VAT, Sec 148 WHT) using Decimal arithmetic.",
)
def calculate_pakistan_customs_duties(
    assessed_value: str = "100.00",
    customs_duty_pct: str = "20.00",
    additional_customs_duty_pct: str | None = None,
    regulatory_duty_pct: str = "0.00",
    federal_excise_duty_pct: str = "0.00",
    sales_tax_pct: str = "18.00",
    value_addition_tax_pct: str = "3.00",
    advance_income_tax_pct: str = "5.50",
) -> dict[str, Any]:
    breakdown = calculate_cascading_customs_taxes(
        assessed_value=Decimal(assessed_value),
        customs_duty_pct=Decimal(customs_duty_pct),
        additional_customs_duty_pct=(
            Decimal(additional_customs_duty_pct)
            if additional_customs_duty_pct is not None
            else None
        ),
        regulatory_duty_pct=Decimal(regulatory_duty_pct),
        federal_excise_duty_pct=Decimal(federal_excise_duty_pct),
        sales_tax_pct=Decimal(sales_tax_pct),
        value_addition_tax_pct=Decimal(value_addition_tax_pct),
        advance_income_tax_pct=Decimal(advance_income_tax_pct),
    )
    return {
        "breakdown": breakdown.to_dict(),
        "formula_steps": format_cascading_steps(breakdown),
    }


@mcp.tool(
    name="get_textile_export_checklist",
    description="Return the deterministic export document checklist for a supported Pakistan textile product and destination.",
)
def get_textile_export_checklist(
    question: str, destination: str | None = None
) -> dict[str, Any]:
    with _db_session() as db:
        call = checklist_tool.maybe_run_checklist_tool(
            db, standalone_question=question, destination=destination, client=None
        )
    if call is None:
        return {"matched": False, "reason": "No single supported textile product matched."}
    tool_result, _, citations = call
    return {
        "matched": True,
        "pct_code": tool_result.pct_code,
        "product": tool_result.product,
        "destination": tool_result.destination,
        "required_documents": [
            {
                "display_name": d.display_name,
                "requirement": d.requirement,
                "condition": d.condition,
            }
            for d in tool_result.required_documents
        ],
        "conditional_documents": [
            {
                "display_name": d.display_name,
                "requirement": d.requirement,
                "condition": d.condition,
            }
            for d in tool_result.conditional_documents
        ],
        "citations": [
            {
                "document": c.document,
                "reference": c.reference,
                "source_kind": c.source_kind,
                "is_official": c.is_official,
            }
            for c in citations
        ],
    }


@mcp.tool(
    name="search_pakistan_customs_regulations",
    description="Run hybrid BM25 + dense vector + CrossEncoder search over official Pakistan Customs regulations.",
)
def search_pakistan_customs_regulations(
    query: str, pct_code: str | None = None, top_k: int = 3
) -> dict[str, Any]:
    with _db_session() as db:
        output = search_regulatory_evidence(
            db, query=query, pct_code=pct_code, top_k=top_k, verified_only=False
        )
    return {
        "status": output.status,
        "retrieval_mode": output.retrieval_mode,
        "degraded_mode": output.degraded_mode,
        "results": [
            {
                "source_document": item.chunk.source_document,
                "sro_number": item.chunk.sro_number,
                "page_number": item.chunk.page_number,
                "section": item.chunk.section,
                "validation_status": item.chunk.validation_status,
                "excerpt": item.parent.text,
            }
            for item in output.results
        ],
    }
