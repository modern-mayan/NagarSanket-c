from __future__ import annotations

import importlib
import os
from io import BytesIO

from fastapi.testclient import TestClient


def _load_app_module():
    os.environ["USE_FIRESTORE"] = "false"
    module = importlib.import_module("backend.http_server")
    return importlib.reload(module)


def test_create_ticket_success(monkeypatch):
    module = _load_app_module()

    class DummyTicket:
        def model_dump(self):
            return {
                "incident_title": "Test incident",
                "hazard_type": "fallen_wire",
                "severity": "critical",
                "confidence": 0.9,
                "visible_evidence": ["wire on road"],
                "public_risk_summary": "risk",
                "immediate_citizen_action": "stay away",
                "responsible_department": "electricity",
                "ticket_description": "desc",
                "location_text": "17.38, 78.48",
                "escalation_level": "emergency",
            }

    class DummyTrace:
        def model_dump(self):
            return {"model_version": "test-model", "response_id": "abc"}

    monkeypatch.setattr(
        module,
        "generate_civic_ticket_with_trace",
        lambda **kwargs: (DummyTicket(), DummyTrace()),
    )

    client = TestClient(module.app)
    resp = client.post(
        "/api/tickets",
        data={
            "complaint_text": "wire near school",
            "latitude": "17.385",
            "longitude": "78.4867",
        },
        files={"image": ("test.jpg", BytesIO(b"abc"), "image/jpeg")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["latitude"] == 17.385
    assert body["longitude"] == 78.4867
    assert body["ticket"]["hazard_type"] == "fallen_wire"
    assert body["gemini_trace"]["model_version"] == "test-model"


def test_create_ticket_requires_coords():
    module = _load_app_module()
    client = TestClient(module.app)
    resp = client.post("/api/tickets", data={"complaint_text": "hazard only"})
    assert resp.status_code == 422
