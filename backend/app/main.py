from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes_chat import router as chat_router
from app.api.routes_health import router as health_router
from app.config import get_settings
from app.dependencies import get_rag_service
from app.errors import AppError, app_error_handler


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    if get_rag_service.cache_info().currsize:
        await get_rag_service().search.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Sri Lanka Tax Assistant API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.add_exception_handler(AppError, app_error_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_, __):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "INVALID_REQUEST", "message": "The request is invalid."}},
        )

    app.include_router(health_router)
    app.include_router(chat_router)
    return app


app = create_app()
