class DocumentNotFoundError(Exception):
    """Raised when a document record does not exist."""


class StoredDocumentNotFoundError(Exception):
    """Raised when metadata exists but its PDF file is missing."""


class PdfExtractionError(Exception):
    """Raised when text cannot be extracted from a PDF."""


class UnsupportedDocumentTypeError(Exception):
    """Raised when extraction is requested for an unsupported file type."""


class StructuredExtractionUnavailableError(Exception):
    """Raised when a document has no extracted text to structure."""


class StructuredExtractionConfigurationError(Exception):
    """Raised when the structured-extraction provider is not configured."""


class StructuredExtractionProviderError(Exception):
    """Raised when Groq cannot return a valid structured invoice.

    ``code`` is the stable, machine-readable reason. Callers and tests should
    branch on it rather than on ``str(exc)``: the human-readable message names
    the provider and the schema and is expected to keep changing, whereas the
    code is part of the contract. Three tests previously pinned a message
    string that no longer existed, which reported a healthy code path as broken.
    """

    #: Fallback for callers that raise without classifying the failure.
    code: str = "structured_extraction_failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class StructuredExtractionProviderUnavailableError(StructuredExtractionProviderError):
    """Raised when the provider refused to serve the request at all.

    Rate limits, authentication failures and upstream outages are operational
    conditions, not malformed model output. Kept as a subclass so existing
    ``StructuredExtractionProviderError`` handlers continue to work.
    """

    code: str = "provider_unavailable"


class StructuredExtractionRateLimitedError(
    StructuredExtractionProviderUnavailableError
):
    """Raised when the provider rate-limited the request (HTTP 429).

    Separated from a general outage because the two need opposite advice. A
    token-per-minute limit clears in seconds and the caller should be told
    roughly when to retry; an outage has no known recovery time. Reporting both
    as "temporarily unavailable or free-tier limit" told users their daily quota
    was gone when in fact waiting about half a minute would have worked.

    ``retry_after_seconds`` is whatever the provider stated, or None when it
    said nothing. It is never inferred from a guess.
    """

    code: str = "provider_rate_limited"

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        limit_kind: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.retry_after_seconds = retry_after_seconds
        #: e.g. "tokens per minute" - as reported by the provider, for logs only.
        self.limit_kind = limit_kind


class StructuredExtractionAuthError(StructuredExtractionProviderUnavailableError):
    """Raised when the provider rejected our credentials (HTTP 401/403).

    This is a deployment misconfiguration, not a capacity problem, and retrying
    cannot fix it - so it must not be presented to the user as "try again".
    """

    code: str = "provider_auth_failed"


class ShipmentExtractionInputError(Exception):
    """Raised when Phase 2A document IDs or document roles are incomplete."""
