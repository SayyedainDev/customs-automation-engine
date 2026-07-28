import re
from typing import Literal

# The only five PCT codes supported in this single-user capstone prototype.
SUPPORTED_PCT_PRODUCTS = {
    "52010090": "Raw cotton",
    "52051100": "Cotton yarn",
    "52094200": "Denim fabric",
    "61091000": "Cotton knitted T-shirts",
    "63023110": "Cotton bed sheets",
}

def normalize_pct_code(code: str) -> str:
    """Normalize common formatting like '6109.1000' or '6109 1000' to '61091000'.
    
    This does NOT expand 6-digit codes to 8-digit codes. It only cleans spaces
    and punctuation.
    """
    if not code:
        return ""
    # Remove any non-alphanumeric characters (spaces, dots, hyphens)
    return re.sub(r'[^a-zA-Z0-9]', '', code)

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
                "CACE currently supports only five textile PCT codes. The code you entered is outside this prototype’s configured scope.",
                normalized_code,
                product_description
            )
            
        expected_product = SUPPORTED_PCT_PRODUCTS[normalized_code]
        
        if product_description:
            # Simple check for glaring conflicts. In a real system this would use semantic similarity.
            # For this prototype, we'll check if any major words overlap, or if we just accept it.
            # The spec says: "When product description and PCT conflict, request clarification."
            desc_lower = product_description.lower()
            expected_lower = expected_product.lower()
            
            # Simple conflict detection: if it's "yarn" but the code is "t-shirts"
            conflict = False
            if "yarn" in desc_lower and "yarn" not in expected_lower:
                conflict = True
            elif ("t-shirt" in desc_lower or "tshirt" in desc_lower or "shirts" in desc_lower) and "t-shirts" not in expected_lower:
                conflict = True
            elif "cotton" in expected_lower and "cotton" not in desc_lower and "denim" not in desc_lower:
                pass # Acceptable
            
            if conflict:
                return (
                    False,
                    f"The product description and PCT code appear inconsistent. PCT {normalized_code} is configured for {expected_product}, not {product_description}. Confirm the correct product and code before continuing.",
                    normalized_code,
                    product_description
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
        
    return False, "Please provide a valid PCT code from the five supported textile codes.", None, product_description

