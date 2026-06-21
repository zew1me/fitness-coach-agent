import base64
import logging
import struct
import typing
import zlib
from types import SimpleNamespace
from typing import Any

import pytest
from openai import OpenAIError

from backend.engine import screenshot_analyzer


class FakeVisionResponse:
    """Stand-in for openai's ParsedResponse with only the attributes _call_vision reads."""

    def __init__(
        self,
        *,
        status: str = "completed",
        output_parsed: Any = None,
        output_text: str = "",
        incomplete_reason: str | None = None,
    ) -> None:
        self.status = status
        self.output_parsed = output_parsed
        self.output_text = output_text
        self.incomplete_details = (
            SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None
        )


def make_fake_openai(
    captured: dict[str, Any],
    *,
    response: FakeVisionResponse | None = None,
    error: Exception | None = None,
) -> type:
    class FakeResponses:
        async def parse(self, **kwargs: Any) -> FakeVisionResponse:
            captured["parse_kwargs"] = kwargs
            if error is not None:
                raise error
            assert response is not None
            return response

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs
            self.responses = FakeResponses()

    return FakeAsyncOpenAI


@pytest.mark.asyncio
async def test_extract_from_screenshot_returns_model_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, type]] = []
    model = screenshot_analyzer.TrainingLoadChartExtraction(
        date_range=screenshot_analyzer.ChartDateRange(start="2026-04-20", end="2026-04-26"),
        source_app_hint="intervals.icu",
        series=[
            screenshot_analyzer.TrainingLoadPoint(date="2026-04-20", metric="ctl", value=42),
            screenshot_analyzer.TrainingLoadPoint(date="2026-04-20", metric="atl", value=50),
        ],
    )

    async def fake_call_vision(prompt: str, image_url: str, schema: type) -> Any:
        calls.append((prompt, image_url, schema))
        return model

    monkeypatch.setattr(screenshot_analyzer, "_call_vision", fake_call_vision)

    result = await screenshot_analyzer.extract_from_screenshot(
        "https://example.com/chart.png",
        "training_load_chart",
    )

    assert result.screenshot_type == "training_load_chart"
    assert result.data == model.model_dump()
    assert result.data["source_app_hint"] == "intervals.icu"
    assert calls == [
        (
            screenshot_analyzer.EXTRACT_TRAINING_LOAD_CHART_PROMPT,
            "https://example.com/chart.png",
            screenshot_analyzer.TrainingLoadChartExtraction,
        )
    ]


@pytest.mark.asyncio
async def test_extract_from_screenshot_unsupported_type_skips_vision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_call_vision(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("_call_vision should not be called for unsupported types")

    monkeypatch.setattr(screenshot_analyzer, "_call_vision", fail_call_vision)

    result = await screenshot_analyzer.extract_from_screenshot(
        "https://example.com/plan.png",
        "plan_or_calendar",
    )

    assert result.data == {}
    assert result.raw_response == "Unsupported screenshot type for extraction."


@pytest.mark.asyncio
async def test_extract_from_screenshot_none_result_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_vision(*_args: Any, **_kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(screenshot_analyzer, "_call_vision", fake_call_vision)

    result = await screenshot_analyzer.extract_from_screenshot(
        "https://example.com/activity.png",
        "activity_single",
    )

    assert result.data == {}
    assert result.raw_response == "Vision extraction returned no usable data."


@pytest.mark.asyncio
async def test_call_vision_uses_model_max_tokens_and_high_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    response = FakeVisionResponse(
        output_parsed=screenshot_analyzer.ActivityExtraction(sport="running")
    )

    monkeypatch.setattr(screenshot_analyzer.settings, "openai_api_key", "openai-key")
    monkeypatch.setattr(screenshot_analyzer.settings, "openai_vision_model", "vision-model")
    monkeypatch.setattr(screenshot_analyzer.settings, "openai_vision_timeout_seconds", 17.0)
    monkeypatch.setattr(screenshot_analyzer.settings, "openai_vision_max_output_tokens", 7777)
    monkeypatch.setattr(screenshot_analyzer.settings, "openai_vision_reasoning_effort", "low")
    monkeypatch.setattr(
        screenshot_analyzer, "AsyncOpenAI", make_fake_openai(captured, response=response)
    )

    result = await screenshot_analyzer._call_vision(
        "Extract fields",
        "https://example.com/image.png",
        screenshot_analyzer.ActivityExtraction,
    )

    assert isinstance(result, screenshot_analyzer.ActivityExtraction)
    assert result.sport == "running"
    assert captured["client_kwargs"]["timeout"] == 17.0
    parse_kwargs = captured["parse_kwargs"]
    assert parse_kwargs["model"] == "vision-model"
    assert parse_kwargs["max_output_tokens"] == 7777
    assert parse_kwargs["reasoning"] == {"effort": "low"}
    assert parse_kwargs["text_format"] is screenshot_analyzer.ActivityExtraction
    assert parse_kwargs["input"][0]["content"][1]["detail"] == "high"


@pytest.mark.asyncio
async def test_call_vision_returns_none_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_kwargs: Any) -> Any:
        raise AssertionError("AsyncOpenAI should not be constructed without an API key")

    monkeypatch.setattr(screenshot_analyzer.settings, "openai_api_key", "")
    monkeypatch.setattr(screenshot_analyzer, "AsyncOpenAI", boom)

    result = await screenshot_analyzer._call_vision(
        "Extract fields",
        "https://example.com/image.png",
        screenshot_analyzer.ActivityExtraction,
    )

    assert result is None


@pytest.mark.asyncio
async def test_call_vision_incomplete_returns_none_and_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: dict[str, Any] = {}
    response = FakeVisionResponse(status="incomplete", incomplete_reason="max_output_tokens")

    monkeypatch.setattr(screenshot_analyzer.settings, "openai_api_key", "openai-key")
    monkeypatch.setattr(
        screenshot_analyzer, "AsyncOpenAI", make_fake_openai(captured, response=response)
    )

    with caplog.at_level(logging.WARNING, logger=screenshot_analyzer.logger.name):
        result = await screenshot_analyzer._call_vision(
            "Extract fields",
            "https://example.com/image.png",
            screenshot_analyzer.ActivityExtraction,
        )

    assert result is None
    assert "incomplete" in caplog.text
    assert "max_output_tokens" in caplog.text


@pytest.mark.asyncio
async def test_call_vision_refusal_returns_none_and_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: dict[str, Any] = {}
    response = FakeVisionResponse(output_parsed=None, output_text="I can't help with that image.")

    monkeypatch.setattr(screenshot_analyzer.settings, "openai_api_key", "openai-key")
    monkeypatch.setattr(
        screenshot_analyzer, "AsyncOpenAI", make_fake_openai(captured, response=response)
    )

    with caplog.at_level(logging.WARNING, logger=screenshot_analyzer.logger.name):
        result = await screenshot_analyzer._call_vision(
            "Extract fields",
            "https://example.com/image.png",
            screenshot_analyzer.ActivityExtraction,
        )

    assert result is None
    assert "no parsed output" in caplog.text


@pytest.mark.asyncio
async def test_analyze_screenshot_returns_unknown_when_vision_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(screenshot_analyzer.settings, "openai_api_key", "openai-key")
    monkeypatch.setattr(
        screenshot_analyzer,
        "AsyncOpenAI",
        make_fake_openai(captured, error=OpenAIError("invalid image URL")),
    )

    result = await screenshot_analyzer.analyze_screenshot("https://example.com/private.png")

    assert result.screenshot_type == "unknown"
    assert result.raw_response == "Could not confidently classify this screenshot."
    assert result.data["classification"]["confidence"] == 0.0
    assert captured["parse_kwargs"]["model"] == screenshot_analyzer.settings.openai_vision_model


def _solid_png_data_url(
    width: int = 64, height: int = 64, rgb: tuple[int, int, int] = (30, 60, 90)
) -> str:
    """Build a self-contained PNG data URL with no third-party deps (PIL is not installed)."""
    row = bytes(rgb) * width
    raw = bytearray()
    for _ in range(height):
        raw.append(0)  # PNG filter type 0 (none) per scanline
        raw.extend(row)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolor RGB
    idat = zlib.compress(bytes(raw))
    png = signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not screenshot_analyzer.settings.openai_api_key,
    reason="live OpenAI call requires OPENAI_API_KEY",
)
async def test_call_vision_live_returns_valid_structured_output() -> None:
    """Live round-trip: proves the strict json_schema + image input actually parses
    against the real API. Skipped in CI / when no key is configured."""
    result = await screenshot_analyzer._call_vision(
        screenshot_analyzer.CLASSIFY_PROMPT,
        _solid_png_data_url(),
        screenshot_analyzer.ScreenshotClassificationModel,
    )

    assert isinstance(result, screenshot_analyzer.ScreenshotClassificationModel)
    assert result.screenshot_type in set(typing.get_args(screenshot_analyzer.ScreenshotType))
    assert 0.0 <= result.confidence <= 1.0
