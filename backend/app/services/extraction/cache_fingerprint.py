"""Shared fingerprints for structured-extraction cache entries.

The live evaluation cache and the application-level per-document cache must
agree on what changes a model extraction.  Keeping the digest implementation
here avoids an application dependency on ``scripts`` and prevents the two
caches from drifting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.core.config import get_settings


STRUCTURED_EXTRACTION_CACHE_FORMAT_VERSION = "2"
APP_SOURCE_ROOT = Path(__file__).resolve().parents[2]


def _text_digest(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _application_code_version(source_root: Path = APP_SOURCE_ROOT) -> str:
    """Hash application Python bytes; called once while this module imports."""
    digest = hashlib.sha256()
    for path in sorted(source_root.rglob("*.py")):
        digest.update(path.relative_to(source_root).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


APPLICATION_CODE_VERSION = _application_code_version()


def prompt_version(*prompts: str) -> str:
    """Digest the exact prompt strings used for an extraction."""
    return _text_digest(*prompts)[:16]


def schema_version(*response_models: type[BaseModel]) -> str:
    """Digest every schema and structured-output transport sent to Groq."""
    # Lazy import avoids a module cycle: the services which use fingerprints
    # also import the structured-output transport.
    from app.services.structured_extraction_service import (
        JSON_OBJECT_FALLBACK_INSTRUCTIONS,
        JSON_OBJECT_FALLBACK_SCHEMA_SUFFIX,
        JSON_OBJECT_RESPONSE_FORMAT_TYPE,
        STRICT_RESPONSE_FORMAT_TYPE,
        STRUCTURED_EXTRACTION_MAX_RETRIES,
        STRUCTURED_EXTRACTION_TEMPERATURE,
        _groq_strict_schema,
        _json_transport_example,
    )

    return _text_digest(
        json.dumps(
            {
                "temperature": STRUCTURED_EXTRACTION_TEMPERATURE,
                "max_retries": STRUCTURED_EXTRACTION_MAX_RETRIES,
                "strict_response_format_type": STRICT_RESPONSE_FORMAT_TYPE,
                "json_object_response_format_type": JSON_OBJECT_RESPONSE_FORMAT_TYPE,
                "json_object_fallback_schema_suffix": (
                    JSON_OBJECT_FALLBACK_SCHEMA_SUFFIX
                ),
                "json_object_fallback_instructions": (
                    JSON_OBJECT_FALLBACK_INSTRUCTIONS
                ),
            },
            sort_keys=True,
        ),
        *(
            json.dumps(
                {
                    "strict_schema": _groq_strict_schema(model),
                    "json_transport_example": _json_transport_example(model),
                },
                sort_keys=True,
            )
            for model in response_models
        ),
    )[:16]


def ocr_settings() -> dict[str, Any]:
    """Every configured OCR input that can change recovered page text."""
    settings = get_settings()
    return {
        "executable": settings.ocr_executable,
        "language": settings.ocr_language,
        "dpi": settings.ocr_dpi,
        "page_segmentation_mode": settings.ocr_page_segmentation_mode,
        "timeout_seconds": settings.ocr_timeout_seconds,
        "min_confidence": str(settings.ocr_min_confidence),
    }


def extraction_model() -> str:
    return str(get_settings().groq_model)


@dataclass(frozen=True)
class StructuredExtractionFingerprint:
    """Inputs which must match before stored model output can be reused."""

    extraction_model: str
    prompt_version: str
    schema_version: str
    ocr_settings: dict[str, Any]
    document_text_digest: str | None = None

    @classmethod
    def current(
        cls,
        *,
        prompts: tuple[str, ...],
        response_models: tuple[type[BaseModel], ...],
        schema_names: tuple[str, ...] = (),
        document_text: str | None = None,
    ) -> StructuredExtractionFingerprint:
        return cls(
            extraction_model=extraction_model(),
            prompt_version=prompt_version(
                *prompts,
                *(f"schema_name:{name}" for name in schema_names),
            ),
            schema_version=schema_version(*response_models),
            ocr_settings=ocr_settings(),
            document_text_digest=(
                _text_digest(document_text) if document_text is not None else None
            ),
        )

    def to_json(self) -> dict[str, Any]:
        payload = {
            "extraction_model": self.extraction_model,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "ocr_settings": self.ocr_settings,
            "cache_format_version": STRUCTURED_EXTRACTION_CACHE_FORMAT_VERSION,
        }
        if self.document_text_digest is not None:
            payload["document_text_digest"] = self.document_text_digest
        return payload


def runtime_extraction_cache_capability() -> dict[str, Any]:
    """Non-secret runtime proof used to detect a stale API process."""
    # Lazy imports keep this shared module cycle-free for the extraction
    # services that import it.
    from app.schemas.multi_line_extraction import (
        MultiLineInvoiceCandidates,
        MultiLinePackingListCandidates,
    )
    from app.schemas.supporting_documents import SupportingDocumentCandidates
    from app.services.multi_line_shipment_service import (
        MULTI_LINE_INVOICE_SCHEMA_NAME,
        MULTI_LINE_INVOICE_SYSTEM_PROMPT,
        MULTI_LINE_INVOICE_USER_PROMPT_TEMPLATE,
        MULTI_LINE_PACKING_SCHEMA_NAME,
        MULTI_LINE_PACKING_SYSTEM_PROMPT,
        MULTI_LINE_PACKING_USER_PROMPT_TEMPLATE,
    )
    from app.services.extraction.staged_multi_line import (
        extraction_fingerprint_profile,
    )
    from app.services.supporting_document_service import (
        SUPPORTING_SCHEMA_NAME,
        SUPPORTING_SYSTEM_PROMPT,
        SUPPORTING_USER_PROMPT_TEMPLATE,
    )

    (
        invoice_prompts,
        invoice_models,
        invoice_schema_names,
    ) = extraction_fingerprint_profile(invoice=True)
    (
        packing_prompts,
        packing_models,
        packing_schema_names,
    ) = extraction_fingerprint_profile(invoice=False)
    # Must match the marker multi_line_shipment_service.py folds into its own
    # fingerprint, or this health-check digest silently disagrees with the
    # real one after an EXTRACTION_MODE flip, defeating the stale-process guard.
    mode_marker = f"extraction_mode:{get_settings().extraction_mode}"

    profiles = {
        "commercial_invoice": StructuredExtractionFingerprint.current(
            prompts=(
                MULTI_LINE_INVOICE_SYSTEM_PROMPT,
                MULTI_LINE_INVOICE_USER_PROMPT_TEMPLATE,
                mode_marker,
                *invoice_prompts,
            ),
            response_models=(MultiLineInvoiceCandidates, *invoice_models),
            schema_names=(
                MULTI_LINE_INVOICE_SCHEMA_NAME,
                *invoice_schema_names,
            ),
        ).to_json(),
        "packing_list": StructuredExtractionFingerprint.current(
            prompts=(
                MULTI_LINE_PACKING_SYSTEM_PROMPT,
                MULTI_LINE_PACKING_USER_PROMPT_TEMPLATE,
                mode_marker,
                *packing_prompts,
            ),
            response_models=(MultiLinePackingListCandidates, *packing_models),
            schema_names=(
                MULTI_LINE_PACKING_SCHEMA_NAME,
                *packing_schema_names,
            ),
        ).to_json(),
        "supporting_document": StructuredExtractionFingerprint.current(
            prompts=(SUPPORTING_SYSTEM_PROMPT, SUPPORTING_USER_PROMPT_TEMPLATE),
            response_models=(SupportingDocumentCandidates,),
            schema_names=(SUPPORTING_SCHEMA_NAME,),
        ).to_json(),
    }
    return {
        "cache_enabled": True,
        "application_code_version": APPLICATION_CODE_VERSION,
        "profiles": profiles,
    }
