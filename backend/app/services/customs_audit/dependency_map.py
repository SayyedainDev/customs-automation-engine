"""Field -> check_id dependency registry for human-review corrections.

Deliberately not an LLM guess: this is a static, declarative table plus a
small parser, so "what does correcting this field rerun" is one explicit,
unit-tested answer instead of a per-shipment inference. check_ids here are
copied verbatim from the modules that produce them (arithmetic_checks.py,
line_item_checks.py, general_checks.py, document_checks.py) - grep any of
them to see the check itself.

Two kinds of dependency:

* A **direct** check_id - a specific, always-the-same-id check this field
  feeds (e.g. correcting quantity always reruns ``item_quantity_match``).
* The **regulatory family** - PCT code (and, more weakly, product name)
  select *which* government rules apply at all (see
  ``executable_rule_checks.py`` / ``textile_mvp_executable_rules.json``,
  where every regulatory rule id is namespaced ``xr_<pct-or-topic>_...``).
  Which exact rule ids exist for a shipment is data, not code, and changes
  when the rule file changes - so this module returns the *marker*
  (``regulatory_family=True``) and the caller expands it against the
  check_ids actually present on this shipment (never a hardcoded id list
  that could silently go stale against the rule data).
"""

from __future__ import annotations

from dataclasses import dataclass

# The field-path grammar lives one layer down (app.services.multi_line) so
# both this module and the correction-application code in
# multi_line_shipment_service.py share one definition without customs_audit
# (the higher layer) being imported by a lower one.
from app.services.multi_line.field_paths import (
    InvalidFieldPathError,
    ParsedFieldPath,
    parse_field_path,
)

__all__ = [
    "InvalidFieldPathError",
    "ParsedFieldPath",
    "parse_field_path",
    "FieldDependency",
    "AffectedChecksResult",
    "resolve_affected_checks",
    "is_known_field_path",
]


@dataclass(frozen=True)
class FieldDependency:
    check_ids: tuple[str, ...] = ()
    #: True when this field selects which regulatory rules apply at all
    #: (PCT code, and - more weakly - the product name used in RAG queries).
    regulatory_family: bool = False
    #: True when a correction to this field must re-run regulatory RAG
    #: queries (destination, PCT code, required-document context) - false
    #: for a pure arithmetic/weight correction, so a quantity fix never
    #: re-touches the regulatory retriever.
    regulatory_context_changed: bool = False
    note: str = ""


#: Item-level dependencies, keyed by (document, field). Only fields that can
#: actually be corrected through this workflow are listed; a field_path for
#: anything else is rejected as out of scope (see ``resolve``).
_ITEM_DEPENDENCIES: dict[tuple[str, str], FieldDependency] = {
    ("invoice", "quantity"): FieldDependency(
        check_ids=(
            "positive_quantity",
            "item_quantity_match",
            "item_line_calculation",
            "invoice_line_calculation",
            "sum_line_totals_match_invoice_total",
        ),
    ),
    ("invoice", "unit_price"): FieldDependency(
        check_ids=(
            "positive_unit_price",
            "item_line_calculation",
            "invoice_line_calculation",
            "sum_line_totals_match_invoice_total",
        ),
    ),
    ("invoice", "line_total"): FieldDependency(
        check_ids=(
            "item_line_calculation",
            "invoice_line_calculation",
            "sum_line_totals_match_invoice_total",
        ),
    ),
    ("invoice", "net_weight"): FieldDependency(
        check_ids=("item_net_weight_match", "invoice_net_weight_total", "weight_consistency"),
    ),
    ("invoice", "gross_weight"): FieldDependency(
        check_ids=("item_gross_weight_match", "invoice_gross_weight_total", "weight_consistency"),
    ),
    ("invoice", "pct_code"): FieldDependency(
        check_ids=("item_pct_code_match", "mvp_pct_support"),
        regulatory_family=True,
        regulatory_context_changed=True,
        note="Selects which product regulatory rules and supporting-document requirements apply.",
    ),
    ("invoice", "product_name"): FieldDependency(
        regulatory_family=True,
        regulatory_context_changed=True,
        note="Feeds the regulatory-evidence query text; does not gate a specific check_id on its own.",
    ),
    ("packing_list", "quantity"): FieldDependency(check_ids=("item_quantity_match",)),
    ("packing_list", "net_weight"): FieldDependency(
        check_ids=("item_net_weight_match", "packing_net_weight_total", "weight_consistency"),
    ),
    ("packing_list", "gross_weight"): FieldDependency(
        check_ids=("item_gross_weight_match", "packing_gross_weight_total", "weight_consistency"),
    ),
    ("packing_list", "pct_code"): FieldDependency(
        check_ids=("item_pct_code_match",),
        note="Only used as an identity fallback when the invoice PCT code is missing.",
    ),
}

#: Header-level (no item index) dependencies.
_HEADER_DEPENDENCIES: dict[tuple[str, str], FieldDependency] = {
    ("invoice", "destination_country"): FieldDependency(
        regulatory_family=True,
        regulatory_context_changed=True,
        note="Selects destination-specific rules (e.g. the China Certificate-of-Origin requirement).",
    ),
    ("invoice", "declared_net_weight_total"): FieldDependency(check_ids=("invoice_net_weight_total",)),
    ("invoice", "declared_gross_weight_total"): FieldDependency(check_ids=("invoice_gross_weight_total",)),
    ("invoice", "invoice_total"): FieldDependency(check_ids=("sum_line_totals_match_invoice_total",)),
    ("invoice", "exporter_name"): FieldDependency(
        note="No standalone deterministic check_id; covered by the mandatory full Auditor re-derivation of supporting-document field matches.",
    ),
    ("invoice", "shipment_date"): FieldDependency(
        regulatory_family=True,
        regulatory_context_changed=True,
        note="Selects time-bounded rules (e.g. shipment-within-N-days-of-letter-of-credit requirements).",
    ),
    ("packing_list", "declared_net_weight_total"): FieldDependency(check_ids=("packing_net_weight_total",)),
    ("packing_list", "declared_gross_weight_total"): FieldDependency(check_ids=("packing_gross_weight_total",)),
}

#: check_id prefixes/exact ids that make up "the regulatory family" - expanded
#: against a shipment's own check list, never hardcoded per rule.
_REGULATORY_FAMILY_PREFIXES = ("xr_",)
_REGULATORY_FAMILY_EXACT_IDS = ("mvp_pct_support",)


@dataclass(frozen=True)
class AffectedChecksResult:
    field_path: str
    direct_check_ids: tuple[str, ...]
    regulatory_family: bool
    regulatory_context_changed: bool
    note: str = ""

    def resolve_check_ids(self, present_check_ids: list[str]) -> list[str]:
        """Expand the regulatory-family marker against a shipment's own
        check_ids and return the full, ordered, de-duplicated list."""
        result = list(dict.fromkeys(self.direct_check_ids))
        if self.regulatory_family:
            for check_id in present_check_ids:
                if check_id in result:
                    continue
                if check_id in _REGULATORY_FAMILY_EXACT_IDS or any(
                    check_id.startswith(prefix) for prefix in _REGULATORY_FAMILY_PREFIXES
                ):
                    result.append(check_id)
        return result


def resolve_affected_checks(field_path: str) -> AffectedChecksResult:
    """Return what a correction to ``field_path`` is known to affect.

    Raises ``InvalidFieldPathError`` for a field_path outside the allowed
    grammar, and ``KeyError`` for a field this workflow does not (yet) know
    how to map - both are rejected corrections, never a silent no-op.
    """
    parsed = parse_field_path(field_path)
    table = _ITEM_DEPENDENCIES if parsed.item_index is not None else _HEADER_DEPENDENCIES
    key = (parsed.document, parsed.field)
    if key not in table:
        raise KeyError(f"no dependency mapping for field: {field_path!r}")
    dependency = table[key]
    return AffectedChecksResult(
        field_path=field_path,
        direct_check_ids=dependency.check_ids,
        regulatory_family=dependency.regulatory_family,
        regulatory_context_changed=dependency.regulatory_context_changed,
        note=dependency.note,
    )


def is_known_field_path(field_path: str) -> bool:
    try:
        resolve_affected_checks(field_path)
        return True
    except (InvalidFieldPathError, KeyError):
        return False
