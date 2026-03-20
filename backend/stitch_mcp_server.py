from __future__ import annotations

import base64
from typing import Any

from mcp.server.fastmcp import FastMCP

from backend.civicpulse_core import generate_civic_ticket_with_trace

mcp = FastMCP("civicpulse-nagarsanket-stitch")


@mcp.tool(
    name="generate_civic_action_ticket",
    description=(
        "Generate a structured civic hazard ticket from complaint text, optional image, "
        "and optional coordinates. Applies safety rule overrides before returning JSON."
    ),
)
def generate_civic_action_ticket(
    complaint_text: str,
    latitude: float,
    longitude: float,
    image_base64: str | None = None,
    image_mime_type: str = "image/jpeg",
) -> dict[str, Any]:
    image_bytes = None
    if image_base64:
        image_bytes = base64.b64decode(image_base64)

    ticket, trace = generate_civic_ticket_with_trace(
        complaint_text=complaint_text,
        latitude=latitude,
        longitude=longitude,
        image_bytes=image_bytes,
        image_mime_type=image_mime_type,
    )
    return {"ticket": ticket.model_dump(), "gemini_trace": trace.model_dump()}


@mcp.tool(name="health_check", description="Simple health check.")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
