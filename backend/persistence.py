from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from google.cloud import firestore, storage


class TicketStore(Protocol):
    def list_tickets(self, limit: int = 200) -> list[dict[str, Any]]: ...

    def save_ticket(
        self,
        *,
        latitude: float,
        longitude: float,
        ticket: dict[str, Any],
        gemini_trace: dict[str, Any],
        complaint_text: str,
        reporter_id: str | None,
        image_bytes: bytes | None,
        image_mime_type: str | None,
    ) -> dict[str, Any]: ...


class InMemoryTicketStore:
    def __init__(self, max_items: int = 200) -> None:
        self._items: list[dict[str, Any]] = []
        self._max_items = max_items

    def list_tickets(self, limit: int = 200) -> list[dict[str, Any]]:
        return list(self._items[-limit:])

    def save_ticket(
        self,
        *,
        latitude: float,
        longitude: float,
        ticket: dict[str, Any],
        gemini_trace: dict[str, Any],
        complaint_text: str,
        reporter_id: str | None,
        image_bytes: bytes | None,
        image_mime_type: str | None,
    ) -> dict[str, Any]:
        record = {
            "id": str(uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
            "latitude": latitude,
            "longitude": longitude,
            "ticket": ticket,
            "gemini_trace": gemini_trace,
            "image_url": None,
            "reporter_id": reporter_id,
            "complaint_text": complaint_text,
        }
        self._items.append(record)
        if len(self._items) > self._max_items:
            self._items.pop(0)
        return record


class FirestoreTicketStore:
    def __init__(self) -> None:
        project_id = os.getenv("FIREBASE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project_id:
            raise RuntimeError("Missing FIREBASE_PROJECT_ID/GOOGLE_CLOUD_PROJECT for Firestore.")
        self._project_id = project_id
        self._collection = os.getenv("FIRESTORE_COLLECTION", "tickets")
        self._bucket_name = os.getenv("FIREBASE_STORAGE_BUCKET")
        self._firestore = firestore.Client(project=project_id)
        self._storage = storage.Client(project=project_id)

    def _upload_image(self, ticket_id: str, image_bytes: bytes, image_mime_type: str | None) -> str | None:
        if not self._bucket_name:
            return None
        ext = self._ext_from_mime(image_mime_type)
        blob_path = f"hazard-images/{ticket_id}.{ext}"
        bucket = self._storage.bucket(self._bucket_name)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(image_bytes, content_type=image_mime_type or "application/octet-stream")
        blob.cache_control = "private, max-age=0, no-transform"
        blob.patch()
        try:
            return blob.generate_signed_url(version="v4", expiration=timedelta(days=1), method="GET")
        except Exception:
            return f"gs://{self._bucket_name}/{blob_path}"

    @staticmethod
    def _ext_from_mime(image_mime_type: str | None) -> str:
        mapping = {
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }
        return mapping.get((image_mime_type or "").lower(), "bin")

    def list_tickets(self, limit: int = 200) -> list[dict[str, Any]]:
        docs = (
            self._firestore.collection(self._collection)
            .order_by("created_at_client", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        items = [doc.to_dict() for doc in docs]
        items.reverse()
        return items

    def save_ticket(
        self,
        *,
        latitude: float,
        longitude: float,
        ticket: dict[str, Any],
        gemini_trace: dict[str, Any],
        complaint_text: str,
        reporter_id: str | None,
        image_bytes: bytes | None,
        image_mime_type: str | None,
    ) -> dict[str, Any]:
        ticket_id = str(uuid4())
        created_at = datetime.now(UTC).isoformat()
        image_url = None
        if image_bytes:
            image_url = self._upload_image(ticket_id=ticket_id, image_bytes=image_bytes, image_mime_type=image_mime_type)

        record = {
            "id": ticket_id,
            "created_at": created_at,
            "created_at_client": created_at,
            "created_at_server": firestore.SERVER_TIMESTAMP,
            "latitude": latitude,
            "longitude": longitude,
            "ticket": ticket,
            "gemini_trace": gemini_trace,
            "image_url": image_url,
            "reporter_id": reporter_id,
            "complaint_text": complaint_text,
        }
        self._firestore.collection(self._collection).document(ticket_id).set(record)
        return record


def build_ticket_store() -> TicketStore:
    if os.getenv("USE_FIRESTORE", "true").lower() == "true":
        return FirestoreTicketStore()
    return InMemoryTicketStore()
