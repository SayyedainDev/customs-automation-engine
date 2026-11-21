"""Hypothesis strategies for CACE Pakistan Customs duties and PCT codes."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import strategies as st

from app.services.compliance.duty_calculator import VERIFIED_TEXTILE_TARIFF_RATES


def assessed_values() -> st.SearchStrategy[Decimal]:
    """Generate positive 2-decimal customs assessed values."""
    return st.decimals(
        min_value=Decimal("1.00"),
        max_value=Decimal("10000000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    )


def customs_duty_rates() -> st.SearchStrategy[Decimal]:
    """Generate statutory Customs Duty rates across 0%..35% covering the 20% ACD threshold."""
    return st.decimals(
        min_value=Decimal("0.00"),
        max_value=Decimal("35.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    )


def supplementary_tax_rates() -> st.SearchStrategy[Decimal]:
    """Generate non-negative 2-decimal percentage rates for RD, FED, ST, VAT, or WHT."""
    return st.decimals(
        min_value=Decimal("0.00"),
        max_value=Decimal("25.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    )


def supported_textile_pct_codes() -> st.SearchStrategy[str]:
    """Sample across the complete source-owned verified textile PCT tariff schedule."""
    return st.sampled_from(tuple(VERIFIED_TEXTILE_TARIFF_RATES.keys()))


def non_eight_digit_pct_inputs() -> st.SearchStrategy[str]:
    """Generate open-domain strings whose digit count is never eight."""
    return st.text(min_size=0, max_size=24).filter(
        lambda raw: sum(ch.isdigit() for ch in raw) != 8
    )
