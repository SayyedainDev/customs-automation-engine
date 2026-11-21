"""Property evidence for exact Decimal 2026-27 cascading duty calculations."""

from __future__ import annotations

from decimal import Decimal

from app.services.compliance.duty_calculator import (
    HUNDRED,
    VERIFIED_TEXTILE_TARIFF_RATES,
    calculate_cascading_customs_taxes,
    default_acd_rate_for_cd,
    quantize_money,
    resolve_tariff_rates_for_hs_code,
)
from cace_testing.generators.customs_values import (
    assessed_values,
    customs_duty_rates,
    non_eight_digit_pct_inputs,
    supplementary_tax_rates,
    supported_textile_pct_codes,
)
from cace_testing.harnesses.property_runs import run_property


@run_property(
    value=assessed_values(),
    cd_pct=customs_duty_rates(),
    rd_pct=supplementary_tax_rates(),
    fed_pct=supplementary_tax_rates(),
    st_pct=supplementary_tax_rates(),
    vat_pct=supplementary_tax_rates(),
    wht_pct=supplementary_tax_rates(),
)
def test_cascading_bases_and_conservation_laws_hold(
    value: Decimal,
    cd_pct: Decimal,
    rd_pct: Decimal,
    fed_pct: Decimal,
    st_pct: Decimal,
    vat_pct: Decimal,
    wht_pct: Decimal,
) -> None:
    result = calculate_cascading_customs_taxes(
        assessed_value=value,
        customs_duty_pct=cd_pct,
        regulatory_duty_pct=rd_pct,
        federal_excise_duty_pct=fed_pct,
        sales_tax_pct=st_pct,
        value_addition_tax_pct=vat_pct,
        advance_income_tax_pct=wht_pct,
    )

    expected_acd_pct = Decimal("6.00") if cd_pct >= Decimal("20") else Decimal("2.00")
    assert default_acd_rate_for_cd(cd_pct) == expected_acd_pct
    assert result.additional_customs_duty_pct == expected_acd_pct

    expected_dpv = quantize_money(
        result.assessed_value
        + result.customs_duty
        + result.additional_customs_duty
        + result.regulatory_duty
        + result.federal_excise_duty
    )
    assert result.sales_tax_base == expected_dpv
    assert result.sales_tax == quantize_money(expected_dpv * (st_pct / HUNDRED))
    assert result.value_addition_tax == quantize_money(expected_dpv * (vat_pct / HUNDRED))

    expected_it_base = quantize_money(
        expected_dpv + result.sales_tax + result.value_addition_tax
    )
    assert result.income_tax_base == expected_it_base
    assert result.total_landed_cost == quantize_money(
        result.assessed_value + result.total_duties_and_taxes
    )


@run_property(
    base_value=assessed_values(),
    delta=assessed_values(),
    cd_pct=customs_duty_rates(),
)
def test_total_duties_and_landed_cost_increase_monotonically(
    base_value: Decimal,
    delta: Decimal,
    cd_pct: Decimal,
) -> None:
    low = calculate_cascading_customs_taxes(
        assessed_value=base_value,
        customs_duty_pct=cd_pct,
    )
    high = calculate_cascading_customs_taxes(
        assessed_value=base_value + delta,
        customs_duty_pct=cd_pct,
    )
    assert high.total_duties_and_taxes >= low.total_duties_and_taxes
    assert high.total_landed_cost > low.total_landed_cost


@run_property(pct_code=supported_textile_pct_codes())
def test_supported_textile_pct_codes_resolve_exact_schedule(pct_code: str) -> None:
    resolved = resolve_tariff_rates_for_hs_code(None, pct_code)
    assert resolved is not None
    expected_cd, expected_page = VERIFIED_TEXTILE_TARIFF_RATES[pct_code]
    assert resolved.customs_duty_pct == expected_cd
    assert resolved.source_page == expected_page


@run_property(raw_input=non_eight_digit_pct_inputs())
def test_non_eight_digit_pct_inputs_fail_closed(raw_input: str) -> None:
    assert resolve_tariff_rates_for_hs_code(None, raw_input) is None
