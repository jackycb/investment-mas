"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.api.routes.health import router as health_router
from app.api.routes.research import router as research_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.runtime_checks import validate_runtime_dependencies

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Validate critical runtime dependencies before serving requests."""

    validate_runtime_dependencies(settings)
    yield


app = FastAPI(title="Investment MAS", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(research_router)

WEB_ROOT = Path(__file__).resolve().parent / "web"

if WEB_ROOT.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the built-in research UI."""

    return FileResponse(WEB_ROOT / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Return empty favicon response to avoid noisy 404 logs."""

    return Response(status_code=204)
