import logging
import os

from fastapi import FastAPI, Header, HTTPException

from .run import run

logger = logging.getLogger(__name__)

app = FastAPI(title="EducaMadrid sync trigger")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/run")
def trigger_run(x_sync_token: str | None = Header(default=None, alias="X-Sync-Token")):
    """Runs the EducaMadrid sync on demand. Only reachable from the internal Docker
    network (no published host port); the shared token is an extra layer of defense
    since this endpoint drives browser automation with real EducaMadrid credentials."""
    expected = os.getenv("SYNC_TRIGGER_TOKEN", "").strip()
    if not expected:
        raise HTTPException(500, "SYNC_TRIGGER_TOKEN is not configured on the sync service")
    if x_sync_token != expected:
        raise HTTPException(403, "Invalid sync trigger token")
    try:
        return run()
    except Exception as exc:
        logger.exception("Triggered sync run failed")
        raise HTTPException(500, f"Sync run failed: {exc}") from exc
