import re
from typing import Literal

from app.services.compliance.pct_catalog import supported_pct_products


def _supported() -> dict[str, str]:
    """Supported codes, read from the shared catalog config.

    This was a literal five-entry dict. It is now derived so that adding a code
    to regulatory_data/config/textile_mvp_pct_codes.json cannot leave the
    assistant refusing a code the compliance engine supports.
    """
    return supported_pct_products()


class _SupportedProducts(dict):
    """Mapping view kept for the module-level name existing callers import."""

    def __init__(self) -> None:
        super().__init__(_supported())


SUPPORTED_PCT_PRODUCTS: dict[str, str] = _SupportedProducts()

def normalize_pct_code(code: str) -> str:
    """Normalize common formatting like '6109.1000' or '6109 1000' to '61091000'.
    
    This does NOT expand 6-digit codes to 8-digit codes. It only cleans spaces
    and punctuation.
    """
    if not code:
        return ""
    # Remove any non-alphanumeric characters (spaces, dots, hyphens)
    return re.sub(r'[^a-zA-Z0-9]', '', code)


_DESCRIPTION_NOISE = frozenset(
    "of the and a an for with other in to s heavy light count mill made".split()
)


def _description_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", (text or "").casefold())
        if token and token not in _DESCRIPTION_NOISE
    }


def _conflicting_product(description: str, pct_code: str) -> str | None:
    """The catalog product a description matches better than its own code, if any.

    This replaced a hand-written list of word tests ("if 'yarn' in desc and
    'yarn' not in expected ..."), which was written when five codes existed and
    became wrong as soon as the catalog grew: it flagged "Men's woven cotton
    shirts" against its own code 6205.2090 because the description contains
    "shirts" and the expected name is not "T-shirts".

    A conflict is reported only when some *other* supported product is a
    strictly better match and is a strong match in absolute terms. A vague or
    unfamiliar description is not a conflict - CACE has no basis to overrule the
    code the exporter supplied.
    """
    supplied = _description_tokens(description)
    if not supplied:
        return None
    catalog = supported_pct_products()
    own_score = len(supplied & _description_tokens(catalog.get(pct_code, ""))) / len(supplied)
    best_name, best_score = None, 0.0
    for code, name in catalog.items():
        if code == pct_code:
            continue
        score = len(supplied & _description_tokens(name)) / len(supplied)
        if score > best_score:
            best_name, best_score = name, score
    if best_name and best_score > own_score and best_score >= 0.6:
        return best_name
    return None


def validate_pct_scope(
    pct_code: str | None, product_description: str | None
) -> tuple[bool, str, str | None, str | None]:
    """Validate if the provided PCT and/or product are supported.
    
    Returns: (is_supported, message, normalized_pct, resolved_product)
    """
    if not pct_code and not product_description:
        return False, "Please provide a PCT code or product description.", None, None

    normalized_code = normalize_pct_code(pct_code) if pct_code else None
    
    if normalized_code:
        if len(normalized_code) != 8:
            return False, "CACE requires the full eight-digit configured PCT code.", normalized_code, product_description
            
        if normalized_code not in SUPPORTED_PCT_PRODUCTS:
            return (
                False, 
                f"CACE currently supports {len(SUPPORTED_PCT_PRODUCTS)} validated textile PCT codes. "
                "The code you entered is outside this prototype’s configured scope.",
                normalized_code,
                product_description
            )
            
        expected_product = SUPPORTED_PCT_PRODUCTS[normalized_code]
        
        if product_description:
            conflict_with = _conflicting_product(product_description, normalized_code)
            if conflict_with:
                return (
                    False,
                    f"The product description and PCT code appear inconsistent. PCT "
                    f"{normalized_code} is configured for {expected_product}, but "
                    f"'{product_description}' matches {conflict_with} more closely. "
                    "Confirm the correct product and code before continuing.",
                    normalized_code,
                    product_description,
                )

        return True, "Supported", normalized_code, expected_product

    # Only product provided
    desc_lower = product_description.lower() if product_description else ""
    if "t-shirt" in desc_lower or "tshirt" in desc_lower or "shirts" in desc_lower:
        return False, "The closest supported product is cotton knitted T-shirts under PCT 61091000. Please confirm that this code matches your product documentation.", None, product_description
    elif "yarn" in desc_lower:
        return False, "The closest supported product is cotton yarn under PCT 52051100. Please confirm that this code matches your product documentation.", None, product_description
    elif "denim" in desc_lower:
        return False, "The closest supported product is denim fabric under PCT 52094200. Please confirm that this code matches your product documentation.", None, product_description
    elif "sheet" in desc_lower or "bed" in desc_lower:
        return False, "The closest supported product is cotton bed sheets under PCT 63023110. Please confirm that this code matches your product documentation.", None, product_description
    elif "raw cotton" in desc_lower:
        return False, "The closest supported product is raw cotton under PCT 52010090. Please confirm that this code matches your product documentation.", None, product_description
        
    return False, "Please provide a valid PCT code from the supported textile codes.", None, product_description

