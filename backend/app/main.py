import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import router as api_router
from app.config import get_settings
from app.db import engine

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"
FRONTEND_DIST_INDEX = FRONTEND_DIST_DIR / "index.html"
FRONTEND_LOGO = FRONTEND_DIR / "logo.png"


def _cors_origins() -> list[str]:
    try:
        origins = list(get_settings().cors_origins)
    except Exception:
        origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            "http://localhost:3000",
        ]
    # Include Vercel preview and production hostnames automatically when deployed on Vercel.
    for env_key in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        host = os.environ.get(env_key)
        if host:
            origin = host if host.startswith("http") else f"https://{host}"
            if origin not in origins:
                origins.append(origin)
    return origins


app = FastAPI(
    title="Inventory Management System API",
    version="2.0.0",
    description="Role-based, offline-first stock control API with atomic ledger balances.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Attach baseline browser security headers to every API and static response."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return response


app.include_router(api_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Lightweight liveness probe (does not query the database)."""
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
async def readiness() -> dict[str, str]:
    """Readiness probe verifying active database connectivity."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ok"}


def _frontend_not_built_response() -> HTMLResponse:
    return HTMLResponse(
        status_code=503,
        content=(
            "<!doctype html><html lang='en'><head><meta charset='UTF-8'/>"
            "<title>Service Unavailable — Inventory Management System</title></head>"
            "<body style='font-family:system-ui,sans-serif;padding:2rem;max-width:560px;margin:2rem auto;'>"
            "<h1>Web interface unavailable</h1>"
            "<p>Build the frontend with <code>cd frontend &amp;&amp; npm run build</code>.</p>"
            "</body></html>"
        ),
    )


@app.get("/logo.png", include_in_schema=False)
async def serve_logo() -> FileResponse:
    dist_logo = FRONTEND_DIST_DIR / "logo.png"
    logo_path = dist_logo if dist_logo.exists() else FRONTEND_LOGO
    if not logo_path.exists():
        raise HTTPException(status_code=404, detail="Logo not found")
    return FileResponse(logo_path, media_type="image/png")


NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@app.get("/", include_in_schema=False)
async def serve_index() -> Response:
    if not FRONTEND_DIST_INDEX.exists():
        return _frontend_not_built_response()
    return FileResponse(FRONTEND_DIST_INDEX, media_type="text/html", headers=NO_CACHE_HEADERS)


@app.get("/{asset_path:path}", include_in_schema=False)
async def serve_spa_or_asset(asset_path: str) -> Response:
    """Serve built Vite static assets with SPA client-side routing fallback.

    Blocks dotfiles, source maps, and directory traversal outside frontend/dist, and
    disables caching for service-worker and manifest files so PWA updates apply immediately.
    """
    normalized = asset_path.strip("/")
    if normalized == "api" or normalized.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    parts = Path(normalized).parts
    if any(part.startswith(".") for part in parts) or normalized.endswith(".map"):
        raise HTTPException(status_code=404, detail="Not found")
    if not FRONTEND_DIST_DIR.exists():
        return _frontend_not_built_response()
    dist_root = FRONTEND_DIST_DIR.resolve()
    candidate = (dist_root / normalized).resolve()
    # Guard against path traversal before serving any static file from dist/.
    if candidate.is_relative_to(dist_root) and candidate.is_file():
        headers = (
            NO_CACHE_HEADERS
            if normalized in {"sw.js", "registerSW.js", "manifest.webmanifest", "favicon.png", "index.html"}
            else None
        )
        return FileResponse(candidate, headers=headers)
    # Extensionless paths resolve to index.html for client-side SPA routes.
    if "." not in Path(normalized).name and FRONTEND_DIST_INDEX.exists():
        return FileResponse(FRONTEND_DIST_INDEX, media_type="text/html", headers=NO_CACHE_HEADERS)
    raise HTTPException(status_code=404, detail="Asset not found")
