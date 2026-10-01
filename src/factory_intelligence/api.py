"""Local API foundation. Production ingestion and analytics follow in D1."""

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Factory Intelligence", version="0.1.0")


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
