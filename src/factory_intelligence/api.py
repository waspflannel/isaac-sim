"""Local API foundation. Production ingestion and analytics follow in D1."""

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from factory_intelligence.edge.routes import router

app = FastAPI(title="Factory Intelligence", version="0.1.0")
app.include_router(router)


@app.exception_handler(psycopg.Error)
def database_error(request, error):
    if isinstance(error, (psycopg.errors.UniqueViolation, psycopg.errors.ForeignKeyViolation)):
        return JSONResponse(
            status_code=409, content={"detail": "Identity conflict or unknown agent"}
        )
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


@app.exception_handler(ValueError)
def conflict(request, error):
    return JSONResponse(status_code=409, content={"detail": str(error)})


@app.exception_handler(KeyError)
def missing(request, error):
    return JSONResponse(status_code=404, content={"detail": "Record not found"})


@app.middleware("http")
async def bounded_payload(request: Request, call_next):
    if request.method in ("POST", "PUT"):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 8 * 1024 * 1024:
                return JSONResponse(status_code=413, content={"detail": "Payload exceeds 8 MiB"})
            chunks.append(chunk)
        request._body = b"".join(chunks)
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "factory-intelligence"}


@app.get("/api/ready")
def ready():
    try:
        # Standard PG* environment variables are read by libpq.
        with psycopg.connect(connect_timeout=3, options="-c statement_timeout=3000") as conn:
            conn.execute("SELECT 1").fetchone()
    except psycopg.Error:
        # Database errors can include connection details; keep them out of HTTP responses.
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "database": "unavailable"}
        )
    return {"status": "ready", "database": "connected"}
