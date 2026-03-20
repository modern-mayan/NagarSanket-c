from __future__ import annotations

import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4
import logging

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.civicpulse_core import (
    MAX_IMAGE_BYTES,
    generate_civic_ticket_with_trace,
    get_limits,
)

app = FastAPI(title="CivicPulse NagarSanket API", version="1.0.0")
logger = logging.getLogger("uvicorn.error")

raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
allowed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials="*" not in allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = BASE_DIR / "frontend"
STATIC_DIR = FRONTEND_DIR

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

TICKETS: list[dict[str, Any]] = []
MAX_STORED_TICKETS = 200
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 20
REQUEST_HISTORY: dict[str, deque[float]] = {}
REQUEST_HISTORY_LOCK = Lock()


class GeminiTraceModel(BaseModel):
    model_version: str | None = None
    response_id: str | None = None
    prompt_token_count: int | None = None
    candidates_token_count: int | None = None
    total_token_count: int | None = None


class TicketRecordModel(BaseModel):
    id: str
    created_at: str
    latitude: float
    longitude: float
    ticket: dict[str, Any]
    gemini_trace: GeminiTraceModel


def _client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _enforce_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    with REQUEST_HISTORY_LOCK:
        history = REQUEST_HISTORY.setdefault(client_ip, deque())
        while history and now - history[0] > RATE_LIMIT_WINDOW_SECONDS:
            history.popleft()
        if len(history) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Please retry shortly.")
        history.append(now)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "limits": get_limits()}


@app.get("/")
def home() -> FileResponse:
    file_path = FRONTEND_DIR / "index.html"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found.")
    return FileResponse(file_path)


@app.get("/api/tickets", response_model=list[TicketRecordModel])
def list_tickets() -> list[dict[str, Any]]:
    return TICKETS


@app.post("/api/tickets", response_model=TicketRecordModel)
async def create_ticket(
    request: Request,
    complaint_text: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    image: UploadFile | None = File(None),
) -> dict[str, Any]:
    _enforce_rate_limit(_client_ip(request))

    if not complaint_text.strip():
        raise HTTPException(status_code=400, detail="complaint_text is required.")

    image_bytes: bytes | None = None
    image_mime_type = "image/jpeg"
    if image is not None:
        image_bytes = await image.read(MAX_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image too large. Max 5 MB.")
        if image_bytes:
            image_mime_type = image.content_type or "image/jpeg"

    lat = latitude
    lng = longitude
    logger.info("ticket_request ip=%s lat=%.6f lng=%.6f has_image=%s", _client_ip(request), lat, lng, bool(image_bytes))

    try:
        ticket, trace = generate_civic_ticket_with_trace(
            complaint_text=complaint_text,
            latitude=lat,
            longitude=lng,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        )
    except ValueError as exc:
        logger.warning("ticket_validation_error ip=%s detail=%s", _client_ip(request), str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error("ticket_gemini_error ip=%s detail=%s", _client_ip(request), str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("ticket_unexpected_error ip=%s", _client_ip(request))
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc

    record = {
        "id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "latitude": lat,
        "longitude": lng,
        "ticket": ticket.model_dump(),
        "gemini_trace": trace.model_dump(),
    }
    TICKETS.append(record)
    if len(TICKETS) > MAX_STORED_TICKETS:
        TICKETS.pop(0)
    return record


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.http_server:app", host="0.0.0.0", port=8000, reload=True)
