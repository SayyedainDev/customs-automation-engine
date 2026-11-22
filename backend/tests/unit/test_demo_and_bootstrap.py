"""Regression tests for cold-start runtime bootstrap and demo sample routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db_session
from app.main import app
from app.models.documents import DocumentUploadRecord
from app.models.tariff import TariffLine
from app.services import document_service
from scripts.bootstrap_runtime_data import bootstrap_database


def test_bootstrap_database_loads_tariffs_idempotently(
    isolated_database: Engine,
    tmp_path: Path,
) -> None:
    tariff_file = tmp_path / "tariffs.json"
    tariff_file.write_text(
        json.dumps(
            [
                {
                    "hs_code": "62034200",
                    "description": "Men's or boys' trousers of cotton",
                    "chapter": "62",
                    "heading": "6203",
                    "customs_duty_pct": 20.0,
                    "tariff_year": "FY2025-26",
                    "source_page": 158,
                    "needs_review": False,
                },
                {
                    "hs_code": "61091000",
                    "description": "T-shirts, singlets and other vests, knitted, of cotton",
                    "chapter": "61",
                    "heading": "6109",
                    "customs_duty_pct": 20.0,
                    "tariff_year": "FY2025-26",
                    "source_page": 156,
                    "needs_review": False,
                },
                {
                    "hs_code": "61091000",
                    "description": "Duplicate unreviewed heading row",
                    "chapter": "61",
                    "heading": "6109",
                    "customs_duty_pct": None,
                    "tariff_year": "FY2025-26",
                    "source_page": 156,
                    "needs_review": True,
                },
            ]
        ),
        encoding="utf-8",
    )

    with Session(isolated_database) as db:
        first = bootstrap_database(
            db, tariff_json_path=tariff_file, skip_regulatory=True
        )
        count_after_first = int(
            db.execute(select(func.count()).select_from(TariffLine)).scalar() or 0
        )
        second = bootstrap_database(
            db, tariff_json_path=tariff_file, skip_regulatory=True
        )
        count_after_second = int(
            db.execute(select(func.count()).select_from(TariffLine)).scalar() or 0
        )

    assert first.tariff_lines_loaded == 2
    assert first.tariff_lines_skipped is False
    assert count_after_first == 2
    assert second.tariff_lines_loaded == 0
    assert second.tariff_lines_skipped is True
    assert count_after_second == 2


def test_demo_samples_list_load_and_feature_flag(
    isolated_database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(document_service, "UPLOAD_DIRECTORY", tmp_path / "uploads")
    monkeypatch.setattr(settings, "enable_demo_samples", True)

    async def override_db() -> AsyncIterator[Session]:
        with Session(isolated_database) as db:
            yield db

    app.dependency_overrides[get_db_session] = override_db
    try:
        async def exercise() -> tuple[
            httpx.Response, httpx.Response, httpx.Response, httpx.Response
        ]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                listed = await client.get("/api/v1/demo/samples")
                loaded = await client.post(
                    "/api/v1/demo/samples/clean_cotton_tshirts/load"
                )
                unknown = await client.post("/api/v1/demo/samples/nonexistent/load")
                monkeypatch.setattr(settings, "enable_demo_samples", False)
                disabled = await client.get("/api/v1/demo/samples")
                return listed, loaded, unknown, disabled

        listed, loaded, unknown, disabled = asyncio.run(exercise())
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert listed.status_code == 200
    keys = {item["key"] for item in listed.json()["samples"]}
    assert {"clean_cotton_tshirts", "error_quantity_mismatch"}.issubset(keys)

    assert loaded.status_code == 201
    body = loaded.json()
    assert body["commercial_invoice"]["status"] == "extracted"
    assert body["packing_list"]["status"] == "extracted"
    assert len(body["supporting_documents"]) == 2
    assert body["shipment_date"] == "2026-06-29"

    with Session(isolated_database) as db:
        stored_count = int(
            db.execute(
                select(func.count()).select_from(DocumentUploadRecord)
            ).scalar()
            or 0
        )
    assert stored_count == 4

    assert unknown.status_code == 404
    assert disabled.status_code == 404
