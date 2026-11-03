from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import router
from app.config import get_settings
from app.db import engine
from app.reports import router as reports_router


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND_INDEX = FRONTEND_DIR / "index.html"
FRONTEND_LOGO = FRONTEND_DIR / "logo.png"


def _cors_origins() -> list[str]:
    try:
        return get_settings().cors_origins
    except Exception:
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
        ]


app = FastAPI(title="Inventory Management System API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(reports_router)


@app.get("/", include_in_schema=False)
def serve_frontend() -> FileResponse:
    if not FRONTEND_INDEX.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(FRONTEND_INDEX)


@app.get("/logo.png", include_in_schema=False)
def serve_logo() -> FileResponse:
    if not FRONTEND_LOGO.exists():
        raise HTTPException(status_code=404, detail="Logo not found")
    return FileResponse(FRONTEND_LOGO, media_type="image/png")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def readiness() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503, detail="Database unavailable"
        ) from error
    return {"status": "ok"}
