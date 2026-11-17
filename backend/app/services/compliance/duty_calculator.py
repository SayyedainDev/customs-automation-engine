"""Deterministic 2026–27 Pakistan Customs cascading duty and tax calculator.

All monetary and percentage calculations use ``decimal.Decimal`` with
``ROUND_HALF_UP`` to two decimal places (never binary floating-point ``float``)
to guarantee exact statutory compounding:

1. Assessed Customs Value (V)
2. Customs Duty (CD) = V * cd_pct
3. Additional Customs Duty (ACD, S.R.O. 1063(I)/2026) = V * acd_pct
4. Regulatory Duty (RD, S.R.O. 1064(I)/2026) = V * rd_pct
5. Federal Excise / Special Excise Duty (FED/SED) = (V + CD) * fed_pct
6. Compounded Sales Tax Base (DPV) = V + CD + ACD + RD + FED
7. Sales Tax (ST, Sales Tax Act 1990) = DPV * sales_tax_pct (standard 18%)
8. Value Addition Tax (VAT, 12th Schedule) = DPV * vat_pct (3% commercial, 0% industrial)
9. Compounded Income Tax Base = DPV + ST + VAT
10. Advance Income Tax (WHT, Sec. 148 ITO 2001) = Income Tax Base * income_tax_pct
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from pydantic import BaseModel, Field

MONEY_QUANTUM = Decimal("0.01")
HUNDRED = Decimal("100")


def _to_decimal(value: Decimal | int | float | str | None, default: str = "0.00") -> Decimal:
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def default_acd_rate_for_cd(cd_pct: Decimal) -> Decimal:
    """Statutory Additional Customs Duty (ACD) slab under S.R.O. 1063(I)/2026.

    Standard Pakistan Customs slab mapping when no line-specific ACD override
    is stored: 2% for 0%/3%/11%/16% CD slabs, and 6% for >=20% CD slabs.
    """
    if cd_pct >= Decimal("20"):
        return Decimal("6.00")
    return Decimal("2.00")


@dataclass(frozen=True)
class CascadingTaxBreakdown:
    assessed_value: Decimal
    customs_duty_pct: Decimal
    additional_customs_duty_pct: Decimal
    regulatory_duty_pct: Decimal
    federal_excise_duty_pct: Decimal
    sales_tax_pct: Decimal
    value_addition_tax_pct: Decimal
    advance_income_tax_pct: Decimal
    customs_duty: Decimal
    additional_customs_duty: Decimal
    regulatory_duty: Decimal
    federal_excise_duty: Decimal
    sales_tax_base: Decimal
    sales_tax: Decimal
    value_addition_tax: Decimal
    income_tax_base: Decimal
    advance_income_tax: Decimal
    total_duties_and_taxes: Decimal
    total_landed_cost: Decimal
    effective_rate_pct: Decimal

    def to_dict(self) -> dict[str, str]:
        return {
            "assessed_value": f"{self.assessed_value:.2f}",
            "customs_duty_pct": f"{self.customs_duty_pct:.2f}",
            "additional_customs_duty_pct": f"{self.additional_customs_duty_pct:.2f}",
            "regulatory_duty_pct": f"{self.regulatory_duty_pct:.2f}",
            "federal_excise_duty_pct": f"{self.federal_excise_duty_pct:.2f}",
            "sales_tax_pct": f"{self.sales_tax_pct:.2f}",
            "value_addition_tax_pct": f"{self.value_addition_tax_pct:.2f}",
            "advance_income_tax_pct": f"{self.advance_income_tax_pct:.2f}",
            "customs_duty": f"{self.customs_duty:.2f}",
            "additional_customs_duty": f"{self.additional_customs_duty:.2f}",
            "regulatory_duty": f"{self.regulatory_duty:.2f}",
            "federal_excise_duty": f"{self.federal_excise_duty:.2f}",
            "sales_tax_base": f"{self.sales_tax_base:.2f}",
            "sales_tax": f"{self.sales_tax:.2f}",
            "value_addition_tax": f"{self.value_addition_tax:.2f}",
            "income_tax_base": f"{self.income_tax_base:.2f}",
            "advance_income_tax": f"{self.advance_income_tax:.2f}",
            "total_duties_and_taxes": f"{self.total_duties_and_taxes:.2f}",
            "total_landed_cost": f"{self.total_landed_cost:.2f}",
            "effective_rate_pct": f"{self.effective_rate_pct:.2f}",
        }


class DutyCalculationRequest(BaseModel):
    assessed_value: Decimal = Field(default=Decimal("100.00"), gt=Decimal("0"))
    customs_duty_pct: Decimal = Field(ge=Decimal("0"), le=Decimal("200"))
    additional_customs_duty_pct: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    regulatory_duty_pct: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"), le=Decimal("200"))
    federal_excise_duty_pct: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"), le=Decimal("200"))
    sales_tax_pct: Decimal = Field(default=Decimal("18.00"), ge=Decimal("0"), le=Decimal("100"))
    value_addition_tax_pct: Decimal = Field(default=Decimal("3.00"), ge=Decimal("0"), le=Decimal("100"))
    advance_income_tax_pct: Decimal = Field(default=Decimal("5.50"), ge=Decimal("0"), le=Decimal("100"))
    hs_code: str | None = None


class DutyCalculationResponse(BaseModel):
    hs_code: str | None = None
    breakdown: dict[str, str]
    formula_steps: list[str]


def calculate_cascading_customs_taxes(
    *,
    assessed_value: Decimal | int | float | str = Decimal("100.00"),
    customs_duty_pct: Decimal | int | float | str,
    additional_customs_duty_pct: Decimal | int | float | str | None = None,
    regulatory_duty_pct: Decimal | int | float | str | None = None,
    federal_excise_duty_pct: Decimal | int | float | str | None = None,
    sales_tax_pct: Decimal | int | float | str | None = None,
    value_addition_tax_pct: Decimal | int | float | str | None = None,
    advance_income_tax_pct: Decimal | int | float | str | None = None,
) -> CascadingTaxBreakdown:
    """Compute exact compounded Pakistan Customs duties and taxes using Decimal."""
    v = quantize_money(_to_decimal(assessed_value, "100.00"))
    if v <= Decimal("0"):
        raise ValueError("assessed_value must be greater than zero")

    cd_pct = quantize_money(_to_decimal(customs_duty_pct, "0.00"))
    acd_pct = (
        quantize_money(_to_decimal(additional_customs_duty_pct))
        if additional_customs_duty_pct is not None
        else default_acd_rate_for_cd(cd_pct)
    )
    rd_pct = quantize_money(_to_decimal(regulatory_duty_pct, "0.00"))
    fed_pct = quantize_money(_to_decimal(federal_excise_duty_pct, "0.00"))
    st_pct = quantize_money(
        _to_decimal(sales_tax_pct, "18.00") if sales_tax_pct is not None else Decimal("18.00")
    )
    vat_pct = quantize_money(
        _to_decimal(value_addition_tax_pct, "3.00")
        if value_addition_tax_pct is not None
        else Decimal("3.00")
    )
    wht_pct = quantize_money(
        _to_decimal(advance_income_tax_pct, "5.50")
        if advance_income_tax_pct is not None
        else Decimal("5.50")
    )

    cd = quantize_money(v * (cd_pct / HUNDRED))
    acd = quantize_money(v * (acd_pct / HUNDRED))
    rd = quantize_money(v * (rd_pct / HUNDRED))
    fed = quantize_money((v + cd) * (fed_pct / HUNDRED))

    sales_tax_base = quantize_money(v + cd + acd + rd + fed)
    st = quantize_money(sales_tax_base * (st_pct / HUNDRED))
    vat = quantize_money(sales_tax_base * (vat_pct / HUNDRED))

    income_tax_base = quantize_money(sales_tax_base + st + vat)
    wht = quantize_money(income_tax_base * (wht_pct / HUNDRED))

    total_taxes = quantize_money(cd + acd + rd + fed + st + vat + wht)
    total_landed = quantize_money(v + total_taxes)
    effective_rate = quantize_money((total_taxes / v) * HUNDRED)

    return CascadingTaxBreakdown(
        assessed_value=v,
        customs_duty_pct=cd_pct,
        additional_customs_duty_pct=acd_pct,
        regulatory_duty_pct=rd_pct,
        federal_excise_duty_pct=fed_pct,
        sales_tax_pct=st_pct,
        value_addition_tax_pct=vat_pct,
        advance_income_tax_pct=wht_pct,
        customs_duty=cd,
        additional_customs_duty=acd,
        regulatory_duty=rd,
        federal_excise_duty=fed,
        sales_tax_base=sales_tax_base,
        sales_tax=st,
        value_addition_tax=vat,
        income_tax_base=income_tax_base,
        advance_income_tax=wht,
        total_duties_and_taxes=total_taxes,
        total_landed_cost=total_landed,
        effective_rate_pct=effective_rate,
    )


def format_cascading_steps(b: CascadingTaxBreakdown) -> list[str]:
    return [
        f"1. Assessed Customs Value (V) = {b.assessed_value:.2f}",
        f"2. Customs Duty (CD @ {b.customs_duty_pct:.2f}%) = {b.customs_duty:.2f}",
        f"3. Additional Customs Duty (ACD @ {b.additional_customs_duty_pct:.2f}%, S.R.O. 1063(I)/2026) = {b.additional_customs_duty:.2f}",
        f"4. Regulatory Duty (RD @ {b.regulatory_duty_pct:.2f}%, S.R.O. 1064(I)/2026) = {b.regulatory_duty:.2f}",
        f"5. Federal Excise Duty (FED @ {b.federal_excise_duty_pct:.2f}%) = {b.federal_excise_duty:.2f}",
        f"6. Compounded Sales Tax Base (V + CD + ACD + RD + FED) = {b.sales_tax_base:.2f}",
        f"7. Sales Tax (ST @ {b.sales_tax_pct:.2f}%) = {b.sales_tax:.2f}",
        f"8. Value Addition Tax (VAT @ {b.value_addition_tax_pct:.2f}%) = {b.value_addition_tax:.2f}",
        f"9. Compounded Income Tax Base (DPV + ST + VAT) = {b.income_tax_base:.2f}",
        f"10. Advance Income Tax (Sec. 148 @ {b.advance_income_tax_pct:.2f}%) = {b.advance_income_tax:.2f}",
        f"11. Total Duties & Taxes = {b.total_duties_and_taxes:.2f} (Effective Rate: {b.effective_rate_pct:.2f}%)",
        f"12. Total Landed Cost = {b.total_landed_cost:.2f}",
    ]
