"""The supported textile PCT catalog, read from configuration.

Five separate places used to carry their own literal copy of the five supported
codes: the assistant's scope check, the retrieval query builder, the regulatory
source registry and the console's product dropdown. Adding a code meant editing
all of them and hoping none was missed, and a missed one fails silently - the
assistant would refuse a code the compliance engine actually supports.

There is exactly one source of truth, ``regulatory_data/config/
textile_mvp_pct_codes.json``, which already carried the tariff description,
tariff page and validation status for every code. This module reads it and is
cached, so the catalog is parsed once per process.

Membership here means "the deterministic engine has rules for this code". It is
not the knowledge-corpus boundary - Ask CACE searches every accepted source
regardless of what this catalog contains.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from app.services.compliance.rule_loader import PCT_CONFIG_PATH


@dataclass(frozen=True)
class SupportedProduct:
    pct_code: str
    display_pct_code: str
    simple_product_name: str
    official_tariff_description: str
    textile_category: str
    tariff_source_page: int | None
    source_document: str | None
    validation_status: str


@lru_cache(maxsize=1)
def load_pct_catalog() -> tuple[SupportedProduct, ...]:
    """Every PCT code the deterministic compliance engine supports."""
    data = json.loads(PCT_CONFIG_PATH.read_text(encoding="utf-8"))
    products: list[SupportedProduct] = []
    for entry in data["products"]:
        products.append(
            SupportedProduct(
                pct_code=entry["pct_code"],
                display_pct_code=entry.get("display_pct_code", entry["pct_code"]),
                simple_product_name=entry["simple_product_name"],
                official_tariff_description=entry.get("official_tariff_description", ""),
                # The original five predate the category field.
                textile_category=entry.get("textile_category", "raw_material"),
                tariff_source_page=entry.get("tariff_source_page"),
                source_document=entry.get("source_document"),
                validation_status=entry.get("validation_status", ""),
            )
        )
    return tuple(sorted(products, key=lambda p: p.pct_code))


@lru_cache(maxsize=1)
def supported_pct_products() -> dict[str, str]:
    """``{pct_code: product name}`` for every supported code."""
    return {p.pct_code: p.simple_product_name for p in load_pct_catalog()}


@lru_cache(maxsize=1)
def supported_pct_codes() -> tuple[str, ...]:
    return tuple(p.pct_code for p in load_pct_catalog())


@lru_cache(maxsize=1)
def product_search_hints() -> dict[str, str]:
    """Lower-cased product names used to enrich a retrieval query."""
    return {p.pct_code: p.simple_product_name.casefold() for p in load_pct_catalog()}


@lru_cache(maxsize=1)
def codes_by_category() -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for product in load_pct_catalog():
        grouped.setdefault(product.textile_category, []).append(product.pct_code)
    return {key: tuple(sorted(value)) for key, value in grouped.items()}


@lru_cache(maxsize=1)
def raw_material_codes() -> tuple[str, ...]:
    """Codes whose export policy conditions are raw-cotton specific.

    Kept as a category lookup rather than a literal "52010090" so a second raw
    material would inherit the distinction instead of silently being treated as
    a manufactured product.
    """
    return codes_by_category().get("raw_material", ())


@lru_cache(maxsize=1)
def non_raw_material_codes() -> tuple[str, ...]:
    raw = set(raw_material_codes())
    return tuple(code for code in supported_pct_codes() if code not in raw)


def reset_pct_catalog_cache() -> None:
    """Drop cached config. Used by tests that patch the config file."""
    load_pct_catalog.cache_clear()
    supported_pct_products.cache_clear()
    supported_pct_codes.cache_clear()
    product_search_hints.cache_clear()
    codes_by_category.cache_clear()
    raw_material_codes.cache_clear()
    non_raw_material_codes.cache_clear()
