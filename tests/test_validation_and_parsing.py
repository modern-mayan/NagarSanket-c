import pytest

from backend import civicpulse_core


def test_validate_inputs_rejects_invalid_coordinates() -> None:
    with pytest.raises(ValueError, match="Latitude"):
        civicpulse_core._validate_inputs(
            complaint_text="wire down near road",
            latitude=100.0,
            longitude=77.0,
            image_bytes=None,
            image_mime_type="image/jpeg",
        )


def test_validate_inputs_rejects_large_image() -> None:
    oversized = b"x" * (civicpulse_core.MAX_IMAGE_BYTES + 1)
    with pytest.raises(ValueError, match="Image must be"):
        civicpulse_core._validate_inputs(
            complaint_text="manhole open",
            latitude=28.0,
            longitude=77.0,
            image_bytes=oversized,
            image_mime_type="image/jpeg",
        )


def test_strip_code_fences_from_model_output() -> None:
    raw = """```json
{"hazard_type":"waterlogging"}
```"""
    cleaned = civicpulse_core._strip_code_fences(raw)
    assert cleaned == '{"hazard_type":"waterlogging"}'
