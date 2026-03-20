# Architecture of CivicPulse: NagarSanket

This document outlines the architecture and components of the CivicPulse: NagarSanket project.

## Overview

CivicPulse is a mobile-first civic hazard triage application. It allows users to submit complaints about civic issues (like fallen wires, open manholes) via voice or text, captures geolocations, and uses Google's Gemini AI to generate structured tickets. A backend safety rule engine further validates and assigns severities to these tickets, which are then displayed on a municipal dashboard map.

The architecture is divided into three main components:
1.  **Frontend**: A mobile-responsive web interface.
2.  **Backend Services**: An API server that handles requests and integrates with AI services.
3.  **AI Integration & Rule Engine**: The core processing unit that analyzes complaints and applies business rules.

## Project Structure

```text
NagarSanket-c/
├── frontend/
│   └── index.html               # Mobile-first UI using Tailwind CSS and Leaflet for maps
├── backend/
│   ├── civicpulse_core.py       # Core logic: Pydantic schemas, Gemini API integration, and Safety Rule Engine
│   ├── http_server.py           # FastAPI application serving HTTP endpoints
│   └── stitch_mcp_server.py     # MCP (Model Context Protocol) server endpoint for tool wrapping
├── tests/                       # Automated tests (rule engine + validation)
├── requirements.txt             # Python dependencies
└── pyproject.toml               # Project metadata and configuration
```

## Component Details

### 1. Frontend (`frontend/index.html`)
-   **Technology Stack**: HTML5, Vanilla JavaScript, Tailwind CSS (for styling), Leaflet.js (for map rendering).
-   **Responsibilities**:
    -   Collects user input (text or voice) in multiple languages (English/Hindi/Hinglish).
    -   Captures the user's geolocation via the browser's Geolocation API.
    -   Allows image uploads for the complaint.
    -   Communicates with the backend API to submit complaints and retrieve ticket data.
    -   Visualizes generated tickets on an interactive map dashboard.

### 2. Backend API (`backend/http_server.py`)
-   **Technology Stack**: FastAPI, Uvicorn, Python.
-   **Responsibilities**:
    -   Provides RESTful endpoints for the frontend to interact with.
    -   Handles file uploads (images) and form data.
    -   Acts as a bridge between the client and the core logic (`civicpulse_core.py`).
    -   Enforces basic security measures (input validation, rate limiting headers).

### 3. Core Logic & AI (`backend/civicpulse_core.py`)
-   **Technology Stack**: Python, Pydantic, Google Gemini API.
-   **Responsibilities**:
    -   **Schema Validation**: Uses Pydantic models to strictly define input and output schemas.
    -   **AI Processing**: Calls the Gemini API (`generateContent`) to parse unstructured user input and image data into a structured format (JSON).
    -   **Safety Rule Engine**: Applies hardcoded business rules to the AI-generated ticket to adjust severity and urgency based on specific keywords and hazard types.
        -   *Example*: `fallen_wire` is always forced to `critical` and `emergency`.
        -   *Example*: Presence of keywords like `school` or `children` escalates severity.

### 4. MCP Server (`backend/stitch_mcp_server.py`)
-   **Responsibilities**:
    -   Wraps the core functionality into an MCP-compliant server, allowing external AI agents to interact with the CivicPulse system as a tool.

## Data Flow

1.  **User Input**: User provides text/voice/image and location on the frontend.
2.  **API Request**: Frontend sends a POST request with the data to `http_server.py`.
3.  **Core Processing**:
    -   `http_server.py` forwards the data to `civicpulse_core.py`.
    -   The core logic constructs a prompt and sends it to the **Gemini API**.
    -   Gemini returns a structured ticket based on the Pydantic schema.
    -   The **Safety Rule Engine** processes the returned ticket, overriding severity/urgency if necessary.
4.  **API Response**: The finalized ticket is returned to `http_server.py`, which sends it back to the frontend.
5.  **UI Update**: The frontend displays the ticket details and plots it on the Leaflet map.

## Testing Strategy
-   The `tests/` directory contains unit tests primarily focused on:
    -   Validation of input data and edge cases.
    -   Ensuring the Safety Rule Engine correctly overrides ticket severity based on the defined civic rules.
    -   Formatting verification of model outputs.
