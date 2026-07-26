"""Create the clearly labelled synthetic PDF pair used by the live API demo."""

from pathlib import Path

import pymupdf


OUTPUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "live_demo"

INVOICE_LINES = [
    "SYNTHETIC COMMERCIAL INVOICE - FOR SOFTWARE TESTING ONLY",
    "",
    "Exporter: Karachi Cotton Textiles (Synthetic) Pvt Ltd",
    "Buyer: Shanghai Demo Imports Ltd",
    "Invoice Number: SYN-INV-2026-001",
    "Invoice Date: 2026-07-20",
    "Destination Country: China",
    "Currency: USD",
    "",
    "Line 1",
    "Product: Cotton knitted T-shirts",
    "PCT Code: 6109.1000",
    "Quantity: 100 PCS",
    "Unit Price: USD 5.50",
    "Line Total: USD 550.00",
    "Net Weight: 75.00 KG",
    "Gross Weight: 80.00 KG",
    "",
    "Invoice Total: USD 550.00",
    "Declared Net Weight Total: 75.00 KG",
    "Declared Gross Weight Total: 80.00 KG",
]

PACKING_LINES = [
    "SYNTHETIC PACKING LIST - FOR SOFTWARE TESTING ONLY",
    "",
    "Related Invoice: SYN-INV-2026-001",
    "",
    "Line 1",
    "Product: Cotton knitted T-shirts",
    "PCT Code: 6109.1000",
    "Quantity: 100 PCS",
    "Net Weight: 75.00 KG",
    "Gross Weight: 80.00 KG",
    "Package Count: 10 CARTONS",
    "",
    "Declared Net Weight Total: 75.00 KG",
    "Declared Gross Weight Total: 80.00 KG",
]


def _write_pdf(path: Path, lines: list[str]) -> None:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    y = 64
    for index, line in enumerate(lines):
        font_size = 14 if index == 0 else 11
        page.insert_text((60, y), line, fontsize=font_size, fontname="helv")
        y += 28 if index == 0 else 22
    document.set_metadata(
        {
            "title": lines[0],
            "author": "Enterprise Customs Engine test fixture",
            "subject": "Synthetic data; not a real commercial document",
        }
    )
    document.save(path)
    document.close()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    invoice_path = OUTPUT_DIR / "synthetic_commercial_invoice.pdf"
    packing_path = OUTPUT_DIR / "synthetic_packing_list.pdf"
    _write_pdf(invoice_path, INVOICE_LINES)
    _write_pdf(packing_path, PACKING_LINES)
    print(invoice_path)
    print(packing_path)


if __name__ == "__main__":
    main()
