from app.api.routes import compliance
from app.api.routes import compliance_explain
from app.api.routes import customs_audit
from app.api.routes import documents
from app.api.routes import health
from app.api.routes import multi_line_shipment
from app.api.routes import regulatory_evidence
from app.api.routes import shipments
from app.api.routes import shipment_extraction
from app.api.routes import shipment_search
from app.api.routes import structured_extraction
from app.core.config import get_settings
from fastapi import FastAPI

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=(
        "A bootcamp capstone API for hybrid export-document extraction, "
        "deterministic five-PCT textile compliance checks, regulatory evidence "
        "retrieval, and resumable human-reviewed customs-audit workflows."
    ),
    version="1.0.0",
)

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(shipments.router)
app.include_router(structured_extraction.router)
app.include_router(compliance.router)
app.include_router(shipment_extraction.router)
app.include_router(multi_line_shipment.router)
app.include_router(regulatory_evidence.router)
app.include_router(compliance_explain.router)
app.include_router(customs_audit.router)
app.include_router(shipment_search.router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "Welcome to the Enterprise Customs Engine API!",
        "status": "API is running",
        "documentation": "/docs",
    }
