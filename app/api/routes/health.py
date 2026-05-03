"""Health check endpoint."""

from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return service health."""

    return {
        "service": "investment-mas",
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
    }

