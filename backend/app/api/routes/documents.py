from fastapi import APIRouter


router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
)


@router.get("/")
def list_documents() -> dict[str, object]:
    return {
        "documents": [],
        "message": "No documents have been uploaded yet.",
    }