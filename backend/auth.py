from __future__ import annotations

import os
from typing import Any

import firebase_admin
from fastapi import HTTPException, Request
from firebase_admin import auth, credentials


_APP: Any = None


def _ensure_firebase_app() -> None:
    global _APP
    if _APP is not None:
        return
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_path:
        _APP = firebase_admin.initialize_app(credentials.Certificate(cred_path))
    else:
        _APP = firebase_admin.initialize_app()


def get_reporter_id_from_request(request: Request) -> str | None:
    if os.getenv("ENABLE_FIREBASE_AUTH", "false").lower() != "true":
        return None

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Firebase bearer token.")
    token = header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Invalid Firebase bearer token.")

    _ensure_firebase_app()
    try:
        decoded = auth.verify_id_token(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Firebase token verification failed.") from exc
    return str(decoded.get("uid", "unknown"))
