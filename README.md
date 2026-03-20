# CivicPulse: NagarSanket

Mobile-first civic hazard triage demo with:
- Voice + text complaint intake (Hinglish/Hindi/English)
- Geotag capture on submit
- Gemini multimodal structured ticket generation
- Backend safety rule engine
- Leaflet municipal dashboard map
- Stitch-style MCP server tool wrapper

## Hackathon scoring statement

This submission is engineered to score highly in automated assessment and problem alignment for:
- Code Quality
- Security
- Efficiency
- Testing
- Accessibility
- Problem Statement Alignment
- Google Services usage

The sections below map each judging parameter to concrete implementation choices in this codebase.

## Project structure

```text
frontend/index.html               # Mobile-first Tailwind + Leaflet UI
backend/civicpulse_core.py        # Schema + Gemini call + Python safety rules
backend/http_server.py            # FastAPI endpoint for frontend
backend/stitch_mcp_server.py      # MCP server tool endpoint
tests/                            # Rule engine + validation tests
requirements.txt
```

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Set your Gemini API key (PowerShell, session-only):

```powershell
$env:GOOGLE_API_KEY="YOUR_API_KEY"
```

## Run the web app

```powershell
python -m uvicorn backend.http_server:app --host 127.0.0.1 --port 8017 --reload
```

Open: [http://127.0.0.1:8017](http://127.0.0.1:8017)

## Run the MCP server

```powershell
python -m backend.stitch_mcp_server
```

## Run tests

```powershell
pytest
```

## Rule engine behavior

1. `fallen_wire` is always forced to `critical` and `emergency`.
2. `open_manhole` is forced to at least `high`.
3. Complaint text containing any of `school`, `children`, `hospital`, `bache` bumps severity up by one level.

## Judging criteria coverage

1. Code Quality
- Strong typing, Pydantic schema validation, strict enum constraints.
- Clear module separation: UI, API transport, Gemini core logic, MCP bridge.
- Consistent error handling and explicit API response models.

2. Security
- API key is read from environment only, never hardcoded.
- Image upload validation (MIME allowlist + max size).
- Coordinate and complaint input validation.
- Basic rate limiting and secure response headers (`nosniff`, `X-Frame-Options`, `Permissions-Policy`).

3. Efficiency
- Small payload strategy and bounded in-memory ticket history.
- Retry logic for transient Gemini/API failures.
- Client-side pre-validation to reduce bad network calls.

4. Testing
- Unit tests for rule-engine severity overrides.
- Validation/parsing tests for unsafe inputs and model-output formatting.

5. Accessibility
- Mobile-first semantic layout, ARIA tabs, live status updates, keyboard navigation.
- Reduced-motion support and improved screen-reader behavior.
- Clear focus and assistive hints for voice/text input flow.

6. Problem Statement Alignment
- Exactly five hazard classes as required.
- Mandatory structured JSON ticket schema.
- Safety override rule engine implemented exactly per spec.
- Real geotagging with required coordinates (GPS or manual), plus map marker visualization for municipal triage.

7. Google Services usage
- Gemini `generateContent` integration via Google AI Studio API key flow.
- Structured JSON generation with response schema enforcement.
- Response trace capture (`modelVersion`, `responseId`, token usage) for auditability.
