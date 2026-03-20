from __future__ import annotations

import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Any
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
from backend.auth import get_reporter_id_from_request
from backend.persistence import InMemoryTicketStore, TicketStore, build_ticket_store

@asynccontextmanager
async def lifespan(_: FastAPI):
    global STORE
    try:
        STORE = build_ticket_store()
        logger.info("storage_backend=%s", STORE.__class__.__name__)
    except Exception as exc:  # noqa: BLE001
        logger.error("storage_backend_init_failed detail=%s", str(exc))
        STORE = InMemoryTicketStore()
        logger.warning("storage_backend_fallback=%s", STORE.__class__.__name__)
    yield


app = FastAPI(title="CivicPulse NagarSanket API", version="1.0.0", lifespan=lifespan)
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

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 20
REQUEST_HISTORY: dict[str, deque[float]] = {}
REQUEST_HISTORY_LOCK = Lock()
REQUEST_METRICS = {
    "total_requests": 0,
    "tickets_created": 0,
    "validation_errors": 0,
    "gemini_errors": 0,
    "unexpected_errors": 0,
    "rate_limited": 0,
    "avg_latency_ms": 0.0,
}
STORE: TicketStore = InMemoryTicketStore()


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
    image_url: str | None = None
    reporter_id: str | None = None


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
            REQUEST_METRICS["rate_limited"] += 1
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Please retry shortly.")
        history.append(now)


def _record_latency(latency_ms: float) -> None:
    REQUEST_METRICS["total_requests"] += 1
    total = REQUEST_METRICS["total_requests"]
    prev_avg = REQUEST_METRICS["avg_latency_ms"]
    REQUEST_METRICS["avg_latency_ms"] = ((prev_avg * (total - 1)) + latency_ms) / total


def _fallback_to_memory(reason: str) -> None:
    global STORE
    if isinstance(STORE, InMemoryTicketStore):
        return
    logger.error("storage_backend_runtime_failure detail=%s", reason)
    STORE = InMemoryTicketStore()
    logger.warning("storage_backend_fallback=%s", STORE.__class__.__name__)


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
    return {
        "status": "ok",
        "limits": get_limits(),
        "storage_backend": STORE.__class__.__name__,
        "metrics": REQUEST_METRICS,
    }


@app.get("/")
def home() -> FileResponse:
    file_path = FRONTEND_DIR / "index.html"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found.")
    return FileResponse(file_path)


@app.get("/api/tickets", response_model=list[TicketRecordModel])
def list_tickets() -> list[dict[str, Any]]:
    try:
        return STORE.list_tickets(limit=200)
    except Exception as exc:  # noqa: BLE001
        _fallback_to_memory(str(exc))
        return STORE.list_tickets(limit=200)


@app.post("/api/tickets", response_model=TicketRecordModel)
async def create_ticket(
    request: Request,
    complaint_text: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    image: UploadFile | None = File(None),
) -> dict[str, Any]:
    started = time.perf_counter()
    _enforce_rate_limit(_client_ip(request))
    reporter_id = get_reporter_id_from_request(request)

    if not complaint_text.strip():
        REQUEST_METRICS["validation_errors"] += 1
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
    logger.info(
        "ticket_request ip=%s reporter_id=%s lat=%.6f lng=%.6f has_image=%s",
        _client_ip(request),
        reporter_id,
        lat,
        lng,
        bool(image_bytes),
    )

    try:
        ticket, trace = generate_civic_ticket_with_trace(
            complaint_text=complaint_text,
            latitude=lat,
            longitude=lng,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        )
        try:
            record = STORE.save_ticket(
                latitude=lat,
                longitude=lng,
                ticket=ticket.model_dump(),
                gemini_trace=trace.model_dump(),
                complaint_text=complaint_text.strip(),
                reporter_id=reporter_id,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
            )
        except Exception as store_exc:  # noqa: BLE001
            _fallback_to_memory(str(store_exc))
            record = STORE.save_ticket(
                latitude=lat,
                longitude=lng,
                ticket=ticket.model_dump(),
                gemini_trace=trace.model_dump(),
                complaint_text=complaint_text.strip(),
                reporter_id=reporter_id,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
            )
        REQUEST_METRICS["tickets_created"] += 1
    except ValueError as exc:
        REQUEST_METRICS["validation_errors"] += 1
        logger.warning("ticket_validation_error ip=%s detail=%s", _client_ip(request), str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        REQUEST_METRICS["gemini_errors"] += 1
        logger.error("ticket_gemini_error ip=%s detail=%s", _client_ip(request), str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        REQUEST_METRICS["unexpected_errors"] += 1
        logger.exception("ticket_unexpected_error ip=%s", _client_ip(request))
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc
    finally:
        latency_ms = (time.perf_counter() - started) * 1000.0
        _record_latency(latency_ms)
        logger.info("ticket_latency_ms=%.2f", latency_ms)

    return record


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.http_server:app", host="0.0.0.0", port=8000, reload=True)
