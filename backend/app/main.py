import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api import admin, auth, cases, detection, entities, graph, imports, timeline, transactions, users
from app.core import db as dbmod
from app.core.config import get_settings

log = logging.getLogger("fraudlens")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app() -> FastAPI:
    s = get_settings()
    if s.env != "development" and s.secret_key.startswith("dev-only"):
        raise RuntimeError("FL_SECRET_KEY must be set outside development")
    app = FastAPI(title="FraudLens API", version="1.0.0",
                  description="Financial fraud investigation, visualisation and evidence management platform.",
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["Content-Disposition", "X-Evidence-SHA256", "X-Report-SHA256"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # Do not echo submitted values back (they may contain sensitive data) - only locations and messages.
        errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": "Invalid request", "errors": errors})

    @app.exception_handler(IntegrityError)
    async def integrity_handler(request: Request, exc: IntegrityError):
        log.warning("integrity error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=409, content={"detail": "Conflicts with existing data"})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    for r in (auth.router, users.router, imports.router, transactions.router, detection.router, graph.router,
              entities.router, timeline.router, cases.router, admin.router):
        app.include_router(r, prefix="/api")

    @app.get("/api/health", tags=["system"])
    def health():
        try:
            with dbmod.engine.connect() as c:
                c.execute(text("select 1"))
            return {"status": "ok", "database": "ok"}
        except Exception:
            return JSONResponse(status_code=503, content={"status": "degraded", "database": "unavailable"})

    return app


app = create_app()
