from app.services.assistant.foundation import normalize_pct_code, validate_pct_scope

def test_normalize_pct_code():
    assert normalize_pct_code("6109.1000") == "61091000"
    assert normalize_pct_code("6109 1000") == "61091000"
    assert normalize_pct_code("5201.0090") == "52010090"
    
def test_validate_pct_scope_supported():
    is_valid, msg, code, prod = validate_pct_scope("6109.1000", "Cotton knitted T-shirts")
    assert is_valid is True
    assert code == "61091000"
    assert prod == "Cotton knitted T-shirts"

def test_validate_pct_scope_unsupported():
    is_valid, msg, code, prod = validate_pct_scope("6203.4200", "Some product")
    assert is_valid is False
    assert "CACE currently supports only five textile PCT codes" in msg

def test_validate_pct_scope_conflict():
    is_valid, msg, code, prod = validate_pct_scope("6109.1000", "Cotton yarn")
    assert is_valid is False
    assert "The product description and PCT code appear inconsistent." in msg

def test_validate_pct_scope_6_digit():
    is_valid, msg, code, prod = validate_pct_scope("610910", None)
    assert is_valid is False
    assert "CACE requires the full eight-digit configured PCT code." in msg

def test_validate_pct_scope_product_only():
    is_valid, msg, code, prod = validate_pct_scope(None, "I want to export cotton T-shirts.")
    assert is_valid is False
    assert "The closest supported product is cotton knitted T-shirts under PCT 61091000" in msg
