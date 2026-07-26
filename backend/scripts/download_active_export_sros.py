#!/usr/bin/env python3
"""Dry-run and download Customs Active/Operative Export SRO PDFs from FBR.

Dry-run is the default and only fetches the official index page. Pass
``--download`` explicitly after reviewing the generated Markdown report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import fitz
import httpx
from bs4 import BeautifulSoup


FBR_PAGE_URL = "https://www.fbr.gov.pk/ActiveSrosExport"
FBR_DOWNLOAD_HOST = "download1.fbr.gov.pk"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGULATORY_ROOT = PROJECT_ROOT / "regulatory_data"
DEFAULT_OUTPUT_DIR = (
    REGULATORY_ROOT / "raw" / "fbr" / "export_sros" / "all_active_export_sros"
)
DEFAULT_MANIFEST_PATH = (
    REGULATORY_ROOT
    / "raw"
    / "fbr"
    / "export_sros"
    / "export_sro_download_manifest.json"
)
DEFAULT_REPORT_PATH = (
    REGULATORY_ROOT
    / "raw"
    / "fbr"
    / "export_sros"
    / "export_sro_download_report.md"
)

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
STANDARD_SRO_RE = re.compile(
    r"^\s*(?P<number>\d+)\s*\(\s*I\s*\)\s*/\s*(?P<year>\d{4})\s*$",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"(?P<year>\d{4})")
MAX_FILENAME_LENGTH = 180

# Concise labels for rows currently published on the official page. The full,
# verbatim FBR title remains in the manifest/report, and new rows use the generic
# slugifier automatically.
TITLE_SLUG_OVERRIDES = {
    "2335(I)/2025": "export_development_surcharge_exemption",
    "427(I)/2022": "bazarcha_border_terminal_customs_station",
    "957(I)/2021": "export_facilitation_scheme_2021",
    "988(I)/2021": "amendment_to_sro_212_i_2009",
    "784(I)/2021": "draft_export_oriented_units_sme_rules_amendments",
    "1301(I)/2020": "khalachi_customs_station_rebatable_exports",
    "194(I)/2019": "draft_export_oriented_units_rules_2008_amendment",
    "645(I)/2018": "export_regulatory_duty",
    "646(I)/2018": "supersession_of_sro_811_i_2013",
    "979(I)/2015": "revised_duty_drawback_rates",
    "755(I)/2014": "customs_duty_repayment",
    "323 (I)/2010": "yarn_export_regulatory_duty",
    "----(I)/2010": "copper_aluminium_export_regulatory_duty",
    "888(I)/2009": "export_oriented_units_sme_rules_amendment",
    "805(I)/2009": "wheat_products_export_regulatory_duty_rescission",
    "209(I)/2009": "textile_duty_drawback_rates",
    "210(I)/2009": "leather_sports_goods_duty_drawback_rates",
    "211(I)/2009": "engineering_metal_duty_drawback_rates",
    "212(I)/2009": "miscellaneous_products_duty_drawback_rates",
    "326(I)/2008": "export_oriented_unit_duty_tax_exemption",
    "327(I)/2008": "export_oriented_units_sme_rules_2008",
    "1185(I)/2007": "wheat_products_export_regulatory_duty",
    "1186(I)/2007": "rescission_of_sro_474_i_2006",
    "______(I)/2007": "ata_carnet_rules",
    "482(I)/2007": "ferrous_nonferrous_scrap_export_regulatory_duty",
    "492(I)/2006": "pulses_export_regulatory_duty",
    "1211(I)/2005": "customs_rules_2001_amendment",
    "1080(I)/2005": "earthquake_relief_deemed_exports",
    "1065(I)/2005": "temporary_importation_for_exporters",
    "783(I)/2005": "standard_duty_drawback_notifications_rescission",
    "315(I)/2004": "artificial_leather_duty_drawback",
    "259(I)/2004": "fiber_cement_pipes_duty_drawback",
    "1028(I)/2003": "lubricating_oil_central_excise_drawback",
    "416(I)/2002": "customs_reward_rules",
}


@dataclass(frozen=True)
class SroRow:
    """One official row from the FBR export SRO table."""

    position: int
    sro_number: str
    title: str
    issue_date: str
    source_url: str


@dataclass(frozen=True)
class PlanItem:
    """A dry-run decision for one FBR table row."""

    row: SroRow
    filename: str
    relative_path: str
    action: str
    note: str | None = None


@dataclass
class DownloadResult:
    """Manifest-ready result for one FBR table row."""

    row: SroRow
    filename: str
    relative_path: str
    sha256: str | None
    download_status: str
    downloaded_at: str | None
    note: str | None = None
    duplicate_of: str | None = None

    def to_manifest_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "sro_number": self.row.sro_number,
            "title": self.row.title,
            "issue_date": self.row.issue_date,
            "source_url": self.row.source_url,
            "filename": self.filename,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "download_status": self.download_status,
            "downloaded_at": self.downloaded_at,
        }
        if self.note:
            entry["note"] = self.note
        if self.duplicate_of:
            entry["duplicate_of"] = self.duplicate_of
        return entry


class RetryableHttpStatusError(RuntimeError):
    """Signal that a request should be retried."""


def utc_now() -> str:
    """Return a stable UTC timestamp for reports and manifests."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_whitespace(value: str) -> str:
    """Collapse HTML and formatting whitespace without changing the wording."""

    return " ".join(value.split())


def normalize_issue_date(value: str) -> str:
    """Convert the FBR table date to ISO format, preserving unknown formats."""

    cleaned = normalize_whitespace(value)
    try:
        return datetime.strptime(cleaned, "%b %d %Y").date().isoformat()
    except ValueError:
        return cleaned


def canonicalize_url(value: str, *, base_url: str = FBR_PAGE_URL) -> str:
    """Resolve a row link while preserving its case-sensitive path."""

    absolute = urljoin(base_url, value.strip())
    parsed = urlsplit(absolute)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def is_allowed_download_url(url: str) -> bool:
    """Limit downloads to the official FBR document host over HTTPS."""

    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname == FBR_DOWNLOAD_HOST


def is_direct_pdf_link(url: str) -> bool:
    """Check the URL path only; query strings do not determine file type."""

    return urlsplit(url).path.lower().endswith(".pdf")


def parse_export_sro_rows(html: str, *, page_url: str = FBR_PAGE_URL) -> list[SroRow]:
    """Parse every row from the dedicated Customs Export SRO table."""

    soup = BeautifulSoup(html, "html.parser")
    headings = [normalize_whitespace(node.get_text(" ", strip=True)) for node in soup.find_all("h1")]
    if not any("Customs Active/Operative Notifications/SROs Export" in heading for heading in headings):
        raise ValueError("The expected Customs Active/Operative Export heading was not found")

    table = soup.select_one("table.data")
    if table is None:
        raise ValueError("The expected FBR export SRO table was not found")

    rows: list[SroRow] = []
    for table_row in table.select("tr"):
        cells = table_row.find_all("td", recursive=False)
        if not cells:
            continue
        if len(cells) != 4:
            raise ValueError(f"Unexpected export SRO row with {len(cells)} cells")

        link = cells[1].find("a", href=True)
        if link is None:
            raise ValueError("An export SRO row has no download link")

        position_text = normalize_whitespace(cells[0].get_text(" ", strip=True))
        position_match = re.search(r"\d+", position_text)
        if position_match is None:
            raise ValueError(f"Could not parse row position: {position_text!r}")

        rows.append(
            SroRow(
                position=int(position_match.group()),
                sro_number=normalize_whitespace(link.get_text(" ", strip=True)),
                title=normalize_whitespace(cells[2].get_text(" ", strip=True)),
                issue_date=normalize_issue_date(cells[3].get_text(" ", strip=True)),
                source_url=canonicalize_url(str(link["href"]), base_url=page_url),
            )
        )

    if not rows:
        raise ValueError("The FBR export SRO table contained no data rows")
    return rows


def slugify(value: str) -> str:
    """Return lowercase ASCII snake_case and remove invalid filename characters."""

    value = value.replace("&", " and ").replace("%", " percent ")
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", ascii_value.lower())).strip("_")


def sro_identifier(sro_number: str) -> str:
    """Normalize a real SRO number, or explicitly mark a placeholder as unknown."""

    match = STANDARD_SRO_RE.fullmatch(sro_number)
    if match:
        return f"{match.group('number')}_i_{match.group('year')}"

    year_match = YEAR_RE.search(sro_number)
    if year_match:
        return f"unknown_i_{year_match.group('year')}"
    return "unknown"


def title_identifier(row: SroRow) -> str:
    """Create a concise label while retaining a generic fallback for new rows."""

    override = TITLE_SLUG_OVERRIDES.get(row.sro_number)
    if override:
        return override
    return slugify(row.title) or "untitled"


def make_filename(row: SroRow) -> str:
    """Create a bounded, readable lowercase snake_case PDF filename."""

    prefix = f"sro_{sro_identifier(row.sro_number)}_"
    suffix = ".pdf"
    title_budget = MAX_FILENAME_LENGTH - len(prefix) - len(suffix)
    title_part = title_identifier(row)[:title_budget].rstrip("_") or "untitled"
    return f"{prefix}{title_part}{suffix}"


def relative_to_regulatory(path: Path, *, regulatory_root: Path = REGULATORY_ROOT) -> str:
    """Return a portable manifest path rooted at regulatory_data."""

    return path.resolve().relative_to(regulatory_root.resolve()).as_posix()


def validate_pdf(path: Path) -> tuple[bool, str | None]:
    """Validate both the PDF signature and structure using PyMuPDF."""

    try:
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                return False, "missing %PDF- signature"
        with fitz.open(path) as document:
            if not document.is_pdf:
                return False, "PyMuPDF did not recognize a PDF"
            if document.page_count < 1:
                return False, "PDF contains no pages"
    except (OSError, RuntimeError, ValueError) as exc:
        return False, f"PDF validation failed: {exc}"
    return True, None


def sha256_file(path: Path) -> str:
    """Hash a file without loading it fully into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_filename(filename: str, source_url: str, used: dict[str, str]) -> str:
    """Resolve page-level filename collisions without overwriting another row."""

    if filename not in used or used[filename] == source_url:
        used[filename] = source_url
        return filename

    path = Path(filename)
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:8]
    candidate = f"{path.stem[: MAX_FILENAME_LENGTH - len(path.suffix) - 9]}_{digest}{path.suffix}"
    used[candidate] = source_url
    return candidate


def build_plan(
    rows: list[SroRow],
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    regulatory_root: Path = REGULATORY_ROOT,
) -> list[PlanItem]:
    """Decide what a run would do without requesting any document URL."""

    seen_urls: set[str] = set()
    used_filenames: dict[str, str] = {}
    items: list[PlanItem] = []

    for row in rows:
        filename = unique_filename(make_filename(row), row.source_url, used_filenames)
        destination = output_dir / filename
        relative_path = relative_to_regulatory(destination, regulatory_root=regulatory_root)

        if not is_allowed_download_url(row.source_url):
            action = "rejected_url"
            note = "URL is outside the approved HTTPS FBR download host"
        elif row.source_url in seen_urls:
            action = "duplicate_link"
            note = "The same source URL already appeared in this page"
        elif not is_direct_pdf_link(row.source_url):
            action = "non_pdf_link"
            note = "The official row links to HTML/ZIP rather than directly to a PDF"
        elif destination.exists():
            valid, reason = validate_pdf(destination)
            action = "already_present" if valid else "blocked_existing_target"
            note = reason or ""
        else:
            action = "planned_download"
            note = None

        seen_urls.add(row.source_url)
        items.append(
            PlanItem(
                row=row,
                filename=filename,
                relative_path=relative_path,
                action=action,
                note=note,
            )
        )
    return items


def retry_delay(base_delay: float, attempt: int) -> float:
    """Return capped exponential backoff for a zero-based retry attempt."""

    return min(base_delay * (2**attempt), 30.0)


def fetch_index_page(
    client: httpx.Client,
    *,
    retries: int,
    retry_base_delay: float,
) -> str:
    """Fetch the official export index with bounded retries."""

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.get(FBR_PAGE_URL)
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise RetryableHttpStatusError(f"HTTP {response.status_code}")
            response.raise_for_status()
            return response.text
        except (httpx.TransportError, RetryableHttpStatusError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(retry_delay(retry_base_delay, attempt))
        except httpx.HTTPStatusError:
            raise
    raise RuntimeError(f"Failed to fetch the FBR index after {retries + 1} attempts") from last_error


def download_to_temporary_file(
    client: httpx.Client,
    url: str,
    *,
    output_dir: Path,
    filename: str,
    retries: int,
    retry_base_delay: float,
) -> tuple[Path, str | None]:
    """Stream one response to a unique staging file with retry cleanup."""

    output_dir.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{Path(filename).stem}.",
            suffix=".part",
            dir=output_dir,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            with client.stream("GET", url) as response:
                if response.status_code in RETRYABLE_STATUS_CODES:
                    raise RetryableHttpStatusError(f"HTTP {response.status_code}")
                response.raise_for_status()
                with temporary_path.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                return temporary_path, response.headers.get("content-type")
        except (httpx.TransportError, RetryableHttpStatusError) as exc:
            last_error = exc
            temporary_path.unlink(missing_ok=True)
            if attempt >= retries:
                break
            time.sleep(retry_delay(retry_base_delay, attempt))
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    raise RuntimeError(f"Failed after {retries + 1} attempts: {url}") from last_error


def scan_existing_hashes(regulatory_root: Path) -> dict[str, Path]:
    """Index existing PDFs so a staged duplicate never creates a second copy."""

    hashes: dict[str, Path] = {}
    for path in sorted(regulatory_root.rglob("*.pdf")):
        if path.is_file():
            hashes.setdefault(sha256_file(path), path)
    return hashes


def place_without_overwrite(staged_path: Path, destination: Path) -> None:
    """Place a staged file atomically while refusing an existing destination."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(staged_path, destination)
    except FileExistsError:
        raise FileExistsError(f"Refusing to overwrite existing file: {destination}") from None
    staged_path.unlink()


def execute_downloads(
    plan: list[PlanItem],
    client: httpx.Client,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    regulatory_root: Path = REGULATORY_ROOT,
    delay: float,
    retries: int,
    retry_base_delay: float,
) -> list[DownloadResult]:
    """Execute a reviewed plan without deleting or overwriting organized PDFs."""

    existing_hashes = scan_existing_hashes(regulatory_root)
    results: list[DownloadResult] = []
    made_request = False

    for item in plan:
        row = item.row
        destination = output_dir / item.filename

        if item.action == "already_present":
            file_hash = sha256_file(destination)
            results.append(
                DownloadResult(
                    row=row,
                    filename=destination.name,
                    relative_path=relative_to_regulatory(destination, regulatory_root=regulatory_root),
                    sha256=file_hash,
                    download_status="already_present",
                    downloaded_at=None,
                )
            )
            existing_hashes.setdefault(file_hash, destination)
            continue

        if item.action != "planned_download":
            status = item.action
            results.append(
                DownloadResult(
                    row=row,
                    filename=item.filename,
                    relative_path=item.relative_path,
                    sha256=None,
                    download_status=status,
                    downloaded_at=None,
                    note=item.note,
                )
            )
            continue

        if made_request:
            time.sleep(delay)
        made_request = True

        staged_path: Path | None = None
        try:
            staged_path, content_type = download_to_temporary_file(
                client,
                row.source_url,
                output_dir=output_dir,
                filename=item.filename,
                retries=retries,
                retry_base_delay=retry_base_delay,
            )
            valid_pdf, validation_error = validate_pdf(staged_path)
            if not valid_pdf:
                staged_path.unlink(missing_ok=True)
                results.append(
                    DownloadResult(
                        row=row,
                        filename=item.filename,
                        relative_path=item.relative_path,
                        sha256=None,
                        download_status="non_pdf_response",
                        downloaded_at=None,
                        note=f"{validation_error}; content-type={content_type or 'missing'}",
                    )
                )
                continue

            file_hash = sha256_file(staged_path)
            existing_path = existing_hashes.get(file_hash)
            if existing_path is not None:
                staged_path.unlink(missing_ok=True)
                canonical_relative_path = relative_to_regulatory(
                    existing_path,
                    regulatory_root=regulatory_root,
                )
                results.append(
                    DownloadResult(
                        row=row,
                        filename=existing_path.name,
                        relative_path=canonical_relative_path,
                        sha256=file_hash,
                        download_status="duplicate",
                        downloaded_at=utc_now(),
                        duplicate_of=canonical_relative_path,
                    )
                )
                continue

            place_without_overwrite(staged_path, destination)
            existing_hashes[file_hash] = destination
            results.append(
                DownloadResult(
                    row=row,
                    filename=destination.name,
                    relative_path=relative_to_regulatory(
                        destination,
                        regulatory_root=regulatory_root,
                    ),
                    sha256=file_hash,
                    download_status="downloaded",
                    downloaded_at=utc_now(),
                )
            )
        except Exception as exc:  # Keep processing independent official rows.
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
            results.append(
                DownloadResult(
                    row=row,
                    filename=item.filename,
                    relative_path=item.relative_path,
                    sha256=None,
                    download_status="failed",
                    downloaded_at=None,
                    note=str(exc),
                )
            )

    return results


def markdown_escape(value: str) -> str:
    """Keep table cells readable without allowing pipe characters to split them."""

    return value.replace("|", "\\|").replace("\n", " ")


def render_dry_run_report(plan: list[PlanItem], *, generated_at: str) -> str:
    """Render the mandatory pre-download report."""

    count = lambda action: sum(item.action == action for item in plan)
    lines = [
        "# FBR Active Export SRO Download Report",
        "",
        "**Mode:** DRY RUN — no PDF document URLs were requested and no PDFs were downloaded.  ",
        f"**Generated:** {generated_at}  ",
        f"**Official source:** [{FBR_PAGE_URL}]({FBR_PAGE_URL})",
        "",
        "## Summary",
        "",
        f"- Total SRO links found: **{len(plan)}**",
        f"- PDF downloads planned: **{count('planned_download')}**",
        "- Successfully downloaded: **0**",
        f"- Already present at target path: **{count('already_present')}**",
        f"- Duplicate page links: **{count('duplicate_link')}**",
        "- Checksum duplicates: **0** (known only after staged downloads are hashed)",
        "- Failed downloads: **0** (not attempted)",
        "- Non-PDF responses: **0** (not requested)",
        f"- Non-PDF HTML/ZIP links skipped: **{count('non_pdf_link')}**",
        f"- Rejected off-domain URLs: **{count('rejected_url')}**",
        "",
        "Only rows in the page's **Customs Active/Operative Notifications/SROs Export** table are included. No title keyword filtering is used.",
        "",
        "## Planned actions",
        "",
        "| # | SRO number | Title | Issue date | Proposed filename | Action | Source URL |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for item in plan:
        row = item.row
        action = item.action
        if item.note:
            action = f"{action}: {item.note}"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.position),
                    f"`{markdown_escape(row.sro_number)}`",
                    markdown_escape(row.title),
                    row.issue_date,
                    f"`{item.filename}`",
                    markdown_escape(action),
                    f"[official link]({row.source_url})",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Approval gate",
            "",
            "Review this report first. A later approved run must use the explicit `--download` flag. Dry-run mode does not create the download manifest because no download result or checksum exists yet.",
            "",
        ]
    )
    return "\n".join(lines)


def render_download_report(results: list[DownloadResult], *, generated_at: str) -> str:
    """Render final execution totals and per-row outcomes."""

    statuses: dict[str, int] = {}
    for result in results:
        statuses[result.download_status] = statuses.get(result.download_status, 0) + 1

    lines = [
        "# FBR Active Export SRO Download Report",
        "",
        "**Mode:** DOWNLOAD  ",
        f"**Generated:** {generated_at}  ",
        f"**Official source:** [{FBR_PAGE_URL}]({FBR_PAGE_URL})",
        "",
        "## Summary",
        "",
        f"- Total SRO links found: **{len(results)}**",
        f"- Successfully downloaded: **{statuses.get('downloaded', 0)}**",
        f"- Already present: **{statuses.get('already_present', 0)}**",
        f"- Duplicates: **{statuses.get('duplicate', 0) + statuses.get('duplicate_link', 0)}**",
        f"- Failed downloads: **{statuses.get('failed', 0)}**",
        f"- Non-PDF responses: **{statuses.get('non_pdf_response', 0)}**",
        f"- Non-PDF links skipped: **{statuses.get('non_pdf_link', 0)}**",
        f"- Rejected URLs: **{statuses.get('rejected_url', 0)}**",
        "",
        "## Results",
        "",
        "| # | SRO number | Issue date | Status | Filename / canonical path | SHA-256 | Note |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(result.row.position),
                    f"`{markdown_escape(result.row.sro_number)}`",
                    result.row.issue_date,
                    result.download_status,
                    f"`{markdown_escape(result.relative_path)}`",
                    f"`{result.sha256}`" if result.sha256 else "—",
                    markdown_escape(result.note or result.duplicate_of or ""),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def write_text_atomic(path: Path, content: str) -> None:
    """Replace a script-owned JSON/Markdown artifact atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def write_manifest(results: list[DownloadResult], path: Path, *, generated_at: str) -> None:
    """Write the required machine-readable download manifest."""

    payload = {
        "manifest_version": 1,
        "generated_at": generated_at,
        "source_page": FBR_PAGE_URL,
        "scope": "Customs Active/Operative Notifications/SROs Export only",
        "documents": [result.to_manifest_entry() for result in results],
    }
    write_text_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def create_client(timeout: float) -> httpx.Client:
    """Create an FBR-friendly HTTP client."""

    return httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout),
        headers={
            "User-Agent": "enterprise-customs-engine/1.0 (Pakistan export SRO archival)",
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.1",
        },
    )


def print_dry_run(plan: list[PlanItem], report_path: Path) -> None:
    """Show every planned PDF before any document download can occur."""

    planned = [item for item in plan if item.action == "planned_download"]
    print(f"Dry run complete: {len(plan)} export SRO rows, {len(planned)} direct PDF candidates.")
    print("No PDFs were downloaded. Planned PDF documents:")
    for item in planned:
        print(f"  {item.row.sro_number:<18} -> {item.filename}")
        print(f"    {item.row.source_url}")
    print(f"Dry-run report: {report_path}")


def parse_arguments() -> argparse.Namespace:
    """Parse CLI arguments; dry-run remains the safe default."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download after a separately reviewed dry run. Omit for dry-run mode.",
    )
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between PDF requests")
    parser.add_argument("--retries", type=int, default=3, help="Retries for transient failures")
    parser.add_argument("--retry-base-delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds")
    return parser.parse_args()


def main() -> int:
    """Run a safe dry run or an explicitly approved download pass."""

    args = parse_arguments()
    if args.delay < 0 or args.retries < 0 or args.retry_base_delay < 0 or args.timeout <= 0:
        raise SystemExit("Delay/retry values must be non-negative and timeout must be positive")

    generated_at = utc_now()
    with create_client(args.timeout) as client:
        html = fetch_index_page(
            client,
            retries=args.retries,
            retry_base_delay=args.retry_base_delay,
        )
        rows = parse_export_sro_rows(html)
        plan = build_plan(rows)

        if not args.download:
            report = render_dry_run_report(plan, generated_at=generated_at)
            write_text_atomic(DEFAULT_REPORT_PATH, report)
            print_dry_run(plan, DEFAULT_REPORT_PATH)
            return 0

        results = execute_downloads(
            plan,
            client,
            delay=args.delay,
            retries=args.retries,
            retry_base_delay=args.retry_base_delay,
        )

    write_manifest(results, DEFAULT_MANIFEST_PATH, generated_at=generated_at)
    write_text_atomic(
        DEFAULT_REPORT_PATH,
        render_download_report(results, generated_at=generated_at),
    )
    print(f"Download manifest: {DEFAULT_MANIFEST_PATH}")
    print(f"Download report: {DEFAULT_REPORT_PATH}")
    return 1 if any(result.download_status == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
