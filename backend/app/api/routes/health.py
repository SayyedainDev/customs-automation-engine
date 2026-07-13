from fastapi import APIRouter


router = APIRouter(
    prefix="/health",
    tags=["Health Check"],
)


@router.get("")
def health_check() -> dict[str, str]:
    return {"status": "ok", "message": "API is running"}