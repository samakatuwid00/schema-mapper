"""FastAPI app factory and entrypoint (python -m src.admin_api.app)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..services.common import (ConflictError, NotFoundError, ServiceError,
                               ValidationError)
from . import jobs
from .routers import (actions_router, auth_router, data_router, jobs_router,
                      migrations_router, reads_router, users_router,
                      worker_router)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    jobs.runner()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="schema_mapper admin", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _conflict(request: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def _invalid(request: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ServiceError)
    async def _service(request: Request, exc: ServiceError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    for router in (auth_router, reads_router, data_router, actions_router,
                   jobs_router, worker_router, migrations_router, users_router):
        app.include_router(router)

    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    from .settings import ADMIN_API_HOST, ADMIN_API_PORT
    uvicorn.run("src.admin_api.app:app", host=ADMIN_API_HOST, port=ADMIN_API_PORT)
