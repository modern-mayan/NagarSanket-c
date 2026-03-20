from __future__ import annotations

import base64
import json
import os
import re
import time
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-flash-latest:generateContent"
)

SYSTEM_PROMPT = (
    "You are CivicPulse, an urban hazard triage system. Analyze the provided street image "
    "and user complaint. Classify the hazard strictly into one of the 5 allowed categories. "
    "Provide a structured municipal action ticket. Do not invent exact locations unless "
    "explicitly provided in the text or coordinates. If the image and text conflict, lower "
    "your confidence score. Explain the visible evidence clearly and provide safe, immediate "
    "actions for the citizen."
)

MAX_COMPLAINT_LENGTH = 1500
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class HazardType(str, Enum):
    OPEN_MANHOLE = "open_manhole"
    FALLEN_WIRE = "fallen_wire"
    WATERLOGGING = "waterlogging"
    BROKEN_STREETLIGHT = "broken_streetlight"
    GARBAGE_FIRE_OR_SMOKE = "garbage_fire_or_smoke"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EscalationLevel(str, Enum):
    ROUTINE = "routine"
    URGENT = "urgent"
    EMERGENCY = "emergency"


class CivicTicket(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    incident_title: str
    hazard_type: HazardType
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    visible_evidence: list[str]
    public_risk_summary: str
    immediate_citizen_action: str
    responsible_department: str
    ticket_description: str
    location_text: str
    escalation_level: EscalationLevel

    @field_validator(
        "incident_title",
        "public_risk_summary",
        "immediate_citizen_action",
        "responsible_department",
        "ticket_description",
        "location_text",
    )
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Field cannot be empty.")
        return clean

    @field_validator("visible_evidence")
    @classmethod
    def evidence_non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("visible_evidence cannot be empty.")
        return cleaned


class GeminiTrace(BaseModel):
    model_version: str | None = None
    response_id: str | None = None
    prompt_token_count: int | None = None
    candidates_token_count: int | None = None
    total_token_count: int | None = None


SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
ESCALATION_ORDER = [EscalationLevel.ROUTINE, EscalationLevel.URGENT, EscalationLevel.EMERGENCY]
KEYWORD_BUMP_WORDS = ["school", "children", "hospital", "bache"]
MIN_ESCALATION_BY_SEVERITY = {
    Severity.LOW: EscalationLevel.ROUTINE,
    Severity.MEDIUM: EscalationLevel.ROUTINE,
    Severity.HIGH: EscalationLevel.URGENT,
    Severity.CRITICAL: EscalationLevel.EMERGENCY,
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": [
        "incident_title",
        "hazard_type",
        "severity",
        "confidence",
        "visible_evidence",
        "public_risk_summary",
        "immediate_citizen_action",
        "responsible_department",
        "ticket_description",
        "location_text",
        "escalation_level",
    ],
    "properties": {
        "incident_title": {"type": "STRING"},
        "hazard_type": {"type": "STRING", "enum": [item.value for item in HazardType]},
        "severity": {"type": "STRING", "enum": [item.value for item in Severity]},
        "confidence": {"type": "NUMBER"},
        "visible_evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
        "public_risk_summary": {"type": "STRING"},
        "immediate_citizen_action": {"type": "STRING"},
        "responsible_department": {"type": "STRING"},
        "ticket_description": {"type": "STRING"},
        "location_text": {"type": "STRING"},
        "escalation_level": {"type": "STRING", "enum": [item.value for item in EscalationLevel]},
    },
}


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, count=1, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _validate_inputs(
    complaint_text: str,
    latitude: float | None,
    longitude: float | None,
    image_bytes: bytes | None,
    image_mime_type: str,
) -> tuple[str, float, float]:
    clean_complaint = complaint_text.strip()
    if not clean_complaint:
        raise ValueError("Complaint text is required.")
    if len(clean_complaint) > MAX_COMPLAINT_LENGTH:
        raise ValueError(f"Complaint text must be <= {MAX_COMPLAINT_LENGTH} characters.")

    if latitude is None or longitude is None:
        raise ValueError("Latitude and longitude are required.")
    lat = latitude
    lng = longitude
    if lat < -90 or lat > 90:
        raise ValueError("Latitude must be between -90 and 90.")
    if lng < -180 or lng > 180:
        raise ValueError("Longitude must be between -180 and 180.")

    if image_bytes:
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError(f"Image must be <= {MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
        if image_mime_type.lower() not in ALLOWED_IMAGE_MIME_TYPES:
            allowed = ", ".join(sorted(ALLOWED_IMAGE_MIME_TYPES))
            raise ValueError(f"Unsupported image type '{image_mime_type}'. Allowed: {allowed}.")

    return clean_complaint, lat, lng


def _build_request_payload(
    complaint_text: str,
    latitude: float,
    longitude: float,
    image_bytes: bytes | None,
    image_mime_type: str,
) -> dict[str, Any]:
    prompt = (
        "Return only valid JSON that matches the schema. "
        f"Citizen complaint text: {complaint_text}\n"
        f"Coordinates: latitude={latitude}, longitude={longitude}"
    )
    parts: list[dict[str, Any]] = [{"text": prompt}]
    if image_bytes:
        parts.append(
            {
                "inlineData": {
                    "mimeType": image_mime_type,
                    "data": base64.b64encode(image_bytes).decode("utf-8"),
                }
            }
        )

    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0.2,
        },
    }


def _call_gemini(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY or GEMINI_API_KEY.")

    headers = {"Content-Type": "application/json", "X-goog-api-key": api_key}
    timeout = httpx.Timeout(45.0, connect=10.0)
    last_error: Exception | None = None

    with httpx.Client(timeout=timeout) as client:
        for attempt in range(3):
            try:
                response = client.post(GEMINI_ENDPOINT, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise RuntimeError("Gemini API network error. Please retry.") from exc

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"Gemini API request failed with status {response.status_code}: {response.text}")
            return response.json()

    if last_error:
        raise RuntimeError("Gemini API call failed after retries.") from last_error
    raise RuntimeError("Gemini API call failed after retries.")


def _extract_text_response(raw_response: dict[str, Any]) -> str:
    candidates = raw_response.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini returned no candidates.")
    parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
    if not parts:
        raise ValueError("Gemini response content parts missing.")
    text = parts[0].get("text", "").strip()
    if not text:
        raise ValueError("Gemini text output was empty.")
    return text


def _parse_ticket(raw_response: dict[str, Any]) -> CivicTicket:
    raw_text = _strip_code_fences(_extract_text_response(raw_response))
    try:
        output_json = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned non-JSON output: {raw_text}") from exc

    try:
        return CivicTicket.model_validate(output_json)
    except ValidationError as exc:
        raise ValueError(f"Gemini JSON failed schema validation: {exc}") from exc


def _extract_trace(raw_response: dict[str, Any]) -> GeminiTrace:
    usage = raw_response.get("usageMetadata") or {}
    return GeminiTrace(
        model_version=raw_response.get("modelVersion"),
        response_id=raw_response.get("responseId"),
        prompt_token_count=usage.get("promptTokenCount"),
        candidates_token_count=usage.get("candidatesTokenCount"),
        total_token_count=usage.get("totalTokenCount"),
    )


def _bump_severity_once(value: Severity) -> Severity:
    idx = SEVERITY_ORDER.index(value)
    return SEVERITY_ORDER[min(idx + 1, len(SEVERITY_ORDER) - 1)]


def _ensure_minimum_escalation(current: EscalationLevel, required: EscalationLevel) -> EscalationLevel:
    current_idx = ESCALATION_ORDER.index(current)
    required_idx = ESCALATION_ORDER.index(required)
    return ESCALATION_ORDER[max(current_idx, required_idx)]


def apply_rule_engine(ticket: CivicTicket, complaint_text: str) -> CivicTicket:
    updated = ticket.model_copy(deep=True)
    complaint_lower = complaint_text.lower()

    if updated.hazard_type == HazardType.FALLEN_WIRE:
        updated.severity = Severity.CRITICAL
        updated.escalation_level = EscalationLevel.EMERGENCY

    if updated.hazard_type == HazardType.OPEN_MANHOLE:
        if SEVERITY_ORDER.index(updated.severity) < SEVERITY_ORDER.index(Severity.HIGH):
            updated.severity = Severity.HIGH

    if any(word in complaint_lower for word in KEYWORD_BUMP_WORDS):
        updated.severity = _bump_severity_once(updated.severity)

    required_escalation = MIN_ESCALATION_BY_SEVERITY[updated.severity]
    updated.escalation_level = _ensure_minimum_escalation(updated.escalation_level, required_escalation)
    return updated


def generate_civic_ticket(
    complaint_text: str,
    latitude: float | None,
    longitude: float | None,
    image_bytes: bytes | None,
    image_mime_type: str = "image/jpeg",
) -> CivicTicket:
    ticket, _ = generate_civic_ticket_with_trace(
        complaint_text=complaint_text,
        latitude=latitude,
        longitude=longitude,
        image_bytes=image_bytes,
        image_mime_type=image_mime_type,
    )
    return ticket


def generate_civic_ticket_with_trace(
    complaint_text: str,
    latitude: float | None,
    longitude: float | None,
    image_bytes: bytes | None,
    image_mime_type: str = "image/jpeg",
) -> tuple[CivicTicket, GeminiTrace]:
    clean_complaint, lat, lng = _validate_inputs(
        complaint_text=complaint_text,
        latitude=latitude,
        longitude=longitude,
        image_bytes=image_bytes,
        image_mime_type=image_mime_type,
    )
    payload = _build_request_payload(
        complaint_text=clean_complaint,
        latitude=lat,
        longitude=lng,
        image_bytes=image_bytes,
        image_mime_type=image_mime_type,
    )
    raw_response = _call_gemini(payload)
    ticket = _parse_ticket(raw_response)
    ticket = apply_rule_engine(ticket, clean_complaint)
    if not ticket.location_text.strip() or ticket.location_text.lower() in {"unknown", "n/a"}:
        ticket.location_text = f"{lat:.5f}, {lng:.5f}"
    trace = _extract_trace(raw_response)
    return ticket, trace


def get_limits() -> dict[str, Any]:
    return {
        "max_complaint_length": MAX_COMPLAINT_LENGTH,
        "max_image_bytes": MAX_IMAGE_BYTES,
        "allowed_image_mime_types": sorted(ALLOWED_IMAGE_MIME_TYPES),
    }
