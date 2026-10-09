from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import health as health_repo
from app.db.session import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: the process is up. Never touches the database."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(response: Response, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Readiness probe: 200 only when the database answers.

    A failed ping MUST be a 503 (audit P0-5): load balancers and the compose
    healthcheck treat HTTP 200 as "ready to serve", so returning 200 with
    `{"status": "unavailable"}` sent traffic to a backend whose DB was down.
    """
    try:
        await health_repo.ping(session)
    except Exception:  # noqa: BLE001 - readiness must translate any DB failure into 503
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ready"}
