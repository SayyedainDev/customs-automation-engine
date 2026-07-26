# Synthetic Customs Test Bundle

This bundle contains fictional documents for testing only.

## Files

- `synthetic_commercial_invoice_text.pdf`
- `synthetic_packing_list_text.pdf`
- `synthetic_commercial_invoice_scanned.pdf`
- `synthetic_packing_list_scanned.pdf`
- `expected_extraction_and_checks.json`
- `multi_line_api_request.json`

## Test data

- Product: Cotton knitted T-shirts
- PCT code: 6109.1000
- Quantity: 100 PCS
- Unit price: USD 5.50
- Line total: USD 550.00
- Invoice total: USD 550.00
- Net weight: 75.00 KG
- Gross weight: 80.00 KG
- Destination: China
- Packages: 5 cartons

## How to test

1. Apply your PostgreSQL migrations, including `003_add_extracted_pages.sql` and
   `004_add_ocr_pages.sql`.
2. Install Tesseract before testing the scanned PDFs.
3. Upload the invoice PDF through your existing document upload endpoint.
4. Upload the packing-list PDF through the same endpoint.
5. Copy both returned document UUIDs.
6. Replace the placeholders in `multi_line_api_request.json`.
7. Call:

   `POST /api/v1/compliance/check-documents/multi-line`

## Which PDFs to use

- Use the `_text.pdf` files to test embedded-text extraction.
- Use the `_scanned.pdf` files to test Tesseract OCR fallback.

## Expected behavior

Extraction and invoice-versus-packing-list checks should pass because the values
match. The final legal compliance result may remain `manual_review` when the
regulatory source records are incomplete or unverified. That is expected
fail-closed behavior, not an extraction failure.

All names, addresses, numbers, prices, and shipment details are fictional.
