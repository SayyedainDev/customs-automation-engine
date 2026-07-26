from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
import httpx

from scripts import download_active_export_sros as downloader


PAGE_HTML = """
<html>
  <body>
    <h1>Customs Active/Operative Notifications/SROs Export</h1>
    <table class="data">
      <thead><tr><th>Sr.No</th><th>SRONumber</th><th>Title</th><th>Issue Date</th></tr></thead>
      <tr>
        <td><b>1)</b></td>
        <td><a href="https://download1.fbr.gov.pk/SROs/example.pdf">2335(I)/2025</a></td>
        <td>Exemption of Export Development Surcharge on exports</td>
        <td>Dec 01 2025</td>
      </tr>
      <tr>
        <td><b>2)</b></td>
        <td><a href="https://download1.fbr.gov.pk/SROS/legacy.zip">----(I)/2010</a></td>
        <td>Copper / aluminium: 25% &amp; related goods?</td>
        <td>Mar 13 2010</td>
      </tr>
    </table>
  </body>
</html>
"""


def make_pdf_bytes() -> bytes:
    document = fitz.open()
    document.new_page()
    content = document.tobytes()
    document.close()
    return content


def test_parse_every_export_row_and_preserve_official_metadata() -> None:
    rows = downloader.parse_export_sro_rows(PAGE_HTML)

    assert len(rows) == 2
    assert rows[0] == downloader.SroRow(
        position=1,
        sro_number="2335(I)/2025",
        title="Exemption of Export Development Surcharge on exports",
        issue_date="2025-12-01",
        source_url="https://download1.fbr.gov.pk/SROs/example.pdf",
    )
    assert rows[1].sro_number == "----(I)/2010"
    assert rows[1].source_url == "https://download1.fbr.gov.pk/SROS/legacy.zip"


def test_parser_rejects_a_different_sro_scope() -> None:
    unrelated = PAGE_HTML.replace(
        "Customs Active/Operative Notifications/SROs Export",
        "Income Tax Active SROs",
    )

    try:
        downloader.parse_export_sro_rows(unrelated)
    except ValueError as exc:
        assert "Export heading" in str(exc)
    else:
        raise AssertionError("Expected parser to reject a non-export page")


def test_filename_is_readable_sanitized_and_does_not_invent_placeholder_number() -> None:
    rows = downloader.parse_export_sro_rows(PAGE_HTML)

    assert (
        downloader.make_filename(rows[0])
        == "sro_2335_i_2025_export_development_surcharge_exemption.pdf"
    )
    placeholder_name = downloader.make_filename(rows[1])
    assert placeholder_name == "sro_unknown_i_2010_copper_aluminium_export_regulatory_duty.pdf"
    assert not any(character in placeholder_name for character in '/\\():*?"<>| ')


def test_dry_plan_requests_only_direct_pdf_links(tmp_path: Path) -> None:
    regulatory_root = tmp_path / "regulatory_data"
    output_dir = regulatory_root / "raw/fbr/export_sros/all_active_export_sros"
    rows = downloader.parse_export_sro_rows(PAGE_HTML)

    plan = downloader.build_plan(
        rows,
        output_dir=output_dir,
        regulatory_root=regulatory_root,
    )

    assert [item.action for item in plan] == ["planned_download", "non_pdf_link"]
    assert not output_dir.exists()
    report = downloader.render_dry_run_report(plan, generated_at="2026-07-22T00:00:00Z")
    assert "Total SRO links found: **2**" in report
    assert "PDF downloads planned: **1**" in report
    assert "Non-PDF HTML/ZIP links skipped: **1**" in report


def test_index_fetch_retries_transient_status(monkeypatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, text=PAGE_HTML, request=request)

    monkeypatch.setattr(downloader.time, "sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        html = downloader.fetch_index_page(client, retries=2, retry_base_delay=0)

    assert html == PAGE_HTML
    assert attempts == 2


def test_download_hashes_pdf_and_discards_duplicate_in_same_batch(tmp_path: Path) -> None:
    regulatory_root = tmp_path / "regulatory_data"
    output_dir = regulatory_root / "raw/fbr/export_sros/all_active_export_sros"
    pdf_bytes = make_pdf_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=pdf_bytes,
            headers={"content-type": "application/pdf"},
            request=request,
        )

    first_row = downloader.SroRow(
        position=1,
        sro_number="1(I)/2025",
        title="First export notification",
        issue_date="2025-01-01",
        source_url="https://download1.fbr.gov.pk/SROs/first.pdf",
    )
    second_row = downloader.SroRow(
        position=2,
        sro_number="2(I)/2025",
        title="Same bytes at another official URL",
        issue_date="2025-01-02",
        source_url="https://download1.fbr.gov.pk/SROs/second.pdf",
    )
    plan = downloader.build_plan(
        [first_row, second_row],
        output_dir=output_dir,
        regulatory_root=regulatory_root,
    )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = downloader.execute_downloads(
            plan,
            client,
            output_dir=output_dir,
            regulatory_root=regulatory_root,
            delay=0,
            retries=0,
            retry_base_delay=0,
        )

    expected_hash = hashlib.sha256(pdf_bytes).hexdigest()
    assert [result.download_status for result in results] == ["downloaded", "duplicate"]
    assert [result.sha256 for result in results] == [expected_hash, expected_hash]
    assert len(list(regulatory_root.rglob("*.pdf"))) == 1
    assert not list(regulatory_root.rglob("*.part"))


def test_non_pdf_response_is_not_saved(tmp_path: Path) -> None:
    regulatory_root = tmp_path / "regulatory_data"
    output_dir = regulatory_root / "raw/fbr/export_sros/all_active_export_sros"
    row = downloader.SroRow(
        position=1,
        sro_number="1(I)/2025",
        title="Broken response",
        issue_date="2025-01-01",
        source_url="https://download1.fbr.gov.pk/SROs/broken.pdf",
    )
    plan = downloader.build_plan(
        [row],
        output_dir=output_dir,
        regulatory_root=regulatory_root,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html>not a PDF</html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = downloader.execute_downloads(
            plan,
            client,
            output_dir=output_dir,
            regulatory_root=regulatory_root,
            delay=0,
            retries=0,
            retry_base_delay=0,
        )

    assert results[0].download_status == "non_pdf_response"
    assert not list(regulatory_root.rglob("*.pdf"))
    assert not list(regulatory_root.rglob("*.part"))
