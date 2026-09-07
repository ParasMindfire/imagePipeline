from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.database import SessionLocal
from app.core.queue import get_connection

app = FastAPI(title="Image Job Processing Service")
app.include_router(api_router)


@app.get("/health")
def health():
    """Process-alive check only."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Dependency check — Postgres and RabbitMQ both reachable, not just
    the process being up (an orchestrator routing traffic on /health
    alone can't tell a broken instance from a healthy one)."""
    checks = {"database": False, "rabbitmq": False}

    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = True
        finally:
            db.close()
    except Exception:
        pass

    try:
        conn = get_connection()
        conn.close()
        checks["rabbitmq"] = True
    except Exception:
        pass

    ok = all(checks.values())
    body = {"status": "ok" if ok else "degraded", "checks": checks}
    # An orchestrator decides on the status code, not the JSON body —
    # a 200 with "degraded" inside would still get traffic routed to it.
    # 503 Service Unavailable means:
    return JSONResponse(content=body, status_code=200 if ok else 503)
