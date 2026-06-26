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


class _StatusError(OpenAIError):
    """OpenAIError carrying a `status_code`, like the SDK's APIStatusError subclasses."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeVisionResponse:
    """Stand-in for openai's ParsedResponse with only the attributes _call_vision reads.

    `output_text` is deliberately left as `None` — the SDK can return `None` there on a
    refusal, and the code must read refusal text from `output` parts, never index it.
    """

    def __init__(
        self,
        *,
        status: str = "completed",
        output_parsed: Any = None,
        refusal: str | None = None,
        error: Any = None,
        incomplete_reason: str | None = None,
    ) -> None:
        self.status = status
        self.output_parsed = output_parsed
        self.output_text = None
        self.error = error
        self.incomplete_details = (
            SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None
        )
        self.output = (
            [SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal=refusal)])]
            if refusal is not None
            else []
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

        async def __aenter__(self) -> "FakeAsyncOpenAI":
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            captured["closed"] = True
            return False

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


@pytest.mark.parametrize(
    ("screenshot_type", "expected_prompt", "expected_schema"),
    [
        (
            "activity_single",
            screenshot_analyzer.EXTRACT_ACTIVITY_PROMPT,
            screenshot_analyzer.ActivityExtraction,
        ),
        (
            "wellness_multi_day",
            screenshot_analyzer.EXTRACT_WELLNESS_MULTI_PROMPT,
            screenshot_analyzer.WellnessMultiExtraction,
        ),
        (
            "wellness_single_day",
            screenshot_analyzer.EXTRACT_WELLNESS_SINGLE_PROMPT,
            screenshot_analyzer.WellnessSingleExtraction,
        ),
        (
            "training_load_chart",
            screenshot_analyzer.EXTRACT_TRAINING_LOAD_CHART_PROMPT,
            screenshot_analyzer.TrainingLoadChartExtraction,
        ),
        # plan_or_calendar and unknown both fall through to the generic catch-all.
        (
            "plan_or_calendar",
            screenshot_analyzer.EXTRACT_GENERIC_PROMPT,
            screenshot_analyzer.GenericExtraction,
        ),
        (
            "unknown",
            screenshot_analyzer.EXTRACT_GENERIC_PROMPT,
            screenshot_analyzer.GenericExtraction,
        ),
    ],
)
@pytest.mark.asyncio
async def test_extract_from_screenshot_routes_prompt_and_schema(
    monkeypatch: pytest.MonkeyPatch,
    screenshot_type: screenshot_analyzer.ScreenshotType,
    expected_prompt: str,
    expected_schema: type,
) -> None:
    calls: list[tuple[str, str, type]] = []

    async def fake_call_vision(prompt: str, image_url: str, schema: type) -> Any:
        calls.append((prompt, image_url, schema))
        return schema()

    monkeypatch.setattr(screenshot_analyzer, "_call_vision", fake_call_vision)

    result = await screenshot_analyzer.extract_from_screenshot(
        "https://example.com/shot.png",
        screenshot_type,
    )

    assert result.screenshot_type == screenshot_type
    assert calls == [(expected_prompt, "https://example.com/shot.png", expected_schema)]


@pytest.mark.asyncio
async def test_extract_from_screenshot_preserves_confidence_entry_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = screenshot_analyzer.ActivityExtraction(
        sport="running",
        confidence=[screenshot_analyzer.ConfidenceEntry(field="sport", confidence=0.8)],
    )

    async def fake_call_vision(prompt: str, image_url: str, schema: type) -> Any:
        return model

    monkeypatch.setattr(screenshot_analyzer, "_call_vision", fake_call_vision)

    result = await screenshot_analyzer.extract_from_screenshot(
        "https://example.com/activity.png",
        "activity_single",
    )

    assert result.data["confidence"] == [{"field": "sport", "confidence": 0.8}]


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
    assert parse_kwargs["reasoning"]["effort"] == "low"
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
async def test_call_vision_refusal_returns_none_and_logs_refusal_text(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: dict[str, Any] = {}
    response = FakeVisionResponse(output_parsed=None, refusal="I can't help with that image.")

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
    assert "I can't help with that image." in caplog.text


@pytest.mark.asyncio
async def test_call_vision_no_parsed_output_without_refusal_part_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Regression: output_text can be None on a refusal/content filter. The code must not
    # index it (None[:500] -> TypeError) and must degrade gracefully to None.
    captured: dict[str, Any] = {}
    response = FakeVisionResponse(output_parsed=None)

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
async def test_call_vision_failed_status_returns_none_and_logs_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: dict[str, Any] = {}
    response = FakeVisionResponse(
        status="failed", error=SimpleNamespace(code="server_error", message="upstream blew up")
    )

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
    assert "failed" in caplog.text
    assert "upstream blew up" in caplog.text
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_call_vision_permanent_error_logs_at_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(screenshot_analyzer.settings, "openai_api_key", "openai-key")
    monkeypatch.setattr(
        screenshot_analyzer,
        "AsyncOpenAI",
        make_fake_openai(captured, error=_StatusError("invalid api key", 401)),
    )

    with caplog.at_level(logging.WARNING, logger=screenshot_analyzer.logger.name):
        result = await screenshot_analyzer._call_vision(
            "Extract fields",
            "https://example.com/image.png",
            screenshot_analyzer.ActivityExtraction,
        )

    assert result is None
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_call_vision_transient_error_logs_at_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(screenshot_analyzer.settings, "openai_api_key", "openai-key")
    monkeypatch.setattr(
        screenshot_analyzer,
        "AsyncOpenAI",
        make_fake_openai(captured, error=_StatusError("service unavailable", 503)),
    )

    with caplog.at_level(logging.WARNING, logger=screenshot_analyzer.logger.name):
        result = await screenshot_analyzer._call_vision(
            "Extract fields",
            "https://example.com/image.png",
            screenshot_analyzer.ActivityExtraction,
        )

    assert result is None
    assert caplog.records
    assert all(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_call_vision_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    response = FakeVisionResponse(
        output_parsed=screenshot_analyzer.ActivityExtraction(sport="running")
    )

    monkeypatch.setattr(screenshot_analyzer.settings, "openai_api_key", "openai-key")
    monkeypatch.setattr(
        screenshot_analyzer, "AsyncOpenAI", make_fake_openai(captured, response=response)
    )

    await screenshot_analyzer._call_vision(
        "Extract fields",
        "https://example.com/image.png",
        screenshot_analyzer.ActivityExtraction,
    )

    assert captured["closed"] is True


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
    assert result.data["classification"]["confidence"] == 0.0
    assert captured["parse_kwargs"]["model"] == screenshot_analyzer.settings.openai_vision_model


@pytest.mark.asyncio
async def test_classify_screenshot_maps_model_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_vision(prompt: str, image_url: str, schema: type) -> Any:
        assert prompt == screenshot_analyzer.CLASSIFY_PROMPT
        assert schema is screenshot_analyzer.ScreenshotClassificationModel
        return screenshot_analyzer.ScreenshotClassificationModel(
            screenshot_type="activity_single",
            source_app_hint="Strava",
            date_range_hint="2026-06-01",
            confidence=0.91,
        )

    monkeypatch.setattr(screenshot_analyzer, "_call_vision", fake_call_vision)

    classification = await screenshot_analyzer.classify_screenshot("https://example.com/a.png")

    assert classification.screenshot_type == "activity_single"
    assert classification.source_app_hint == "Strava"
    assert classification.date_range_hint == "2026-06-01"
    assert classification.confidence == 0.91


@pytest.mark.asyncio
async def test_classify_screenshot_none_returns_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_vision(*_args: Any, **_kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(screenshot_analyzer, "_call_vision", fake_call_vision)

    classification = await screenshot_analyzer.classify_screenshot("https://example.com/a.png")

    assert classification.screenshot_type == "unknown"
    assert classification.confidence == 0.0
    assert classification.source_app_hint is None


@pytest.mark.asyncio
async def test_analyze_screenshot_happy_path_extracts_and_attaches_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schemas_seen: list[type] = []

    async def fake_call_vision(prompt: str, image_url: str, schema: type) -> Any:
        schemas_seen.append(schema)
        if schema is screenshot_analyzer.ScreenshotClassificationModel:
            return screenshot_analyzer.ScreenshotClassificationModel(
                screenshot_type="activity_single", confidence=0.9
            )
        return screenshot_analyzer.ActivityExtraction(
            sport="running",
            confidence=[screenshot_analyzer.ConfidenceEntry(field="sport", confidence=0.8)],
        )

    monkeypatch.setattr(screenshot_analyzer, "_call_vision", fake_call_vision)

    result = await screenshot_analyzer.analyze_screenshot("https://example.com/run.png")

    assert result.screenshot_type == "activity_single"
    assert result.data["sport"] == "running"
    assert result.data["confidence"] == [{"field": "sport", "confidence": 0.8}]
    assert result.data["classification"]["screenshot_type"] == "activity_single"
    assert schemas_seen == [
        screenshot_analyzer.ScreenshotClassificationModel,
        screenshot_analyzer.ActivityExtraction,
    ]


@pytest.mark.asyncio
async def test_analyze_screenshot_low_confidence_uses_generic_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schemas_seen: list[type] = []

    async def fake_call_vision(prompt: str, image_url: str, schema: type) -> Any:
        schemas_seen.append(schema)
        if schema is screenshot_analyzer.ScreenshotClassificationModel:
            # Classifier guesses a type but below the confidence floor.
            return screenshot_analyzer.ScreenshotClassificationModel(
                screenshot_type="activity_single", confidence=0.1
            )
        return screenshot_analyzer.GenericExtraction(summary="A weekly plan grid.")

    monkeypatch.setattr(screenshot_analyzer, "_call_vision", fake_call_vision)

    result = await screenshot_analyzer.analyze_screenshot("https://example.com/plan.png")

    assert result.screenshot_type == "unknown"
    assert result.data["summary"] == "A weekly plan grid."
    assert result.data["classification"]["screenshot_type"] == "activity_single"
    assert schemas_seen == [
        screenshot_analyzer.ScreenshotClassificationModel,
        screenshot_analyzer.GenericExtraction,
    ]


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


@pytest.mark.oai
@pytest.mark.asyncio
@pytest.mark.skipif(
    not screenshot_analyzer.settings.openai_api_key,
    reason="live OpenAI call requires OPENAI_API_KEY",
)
async def test_call_vision_live_returns_valid_structured_output() -> None:
    """Live round-trip: proves the strict json_schema + image input actually parses
    against the real API. Opt-in via the `oai` marker (`uv run pytest -m oai`); excluded
    from the default suite and skipped when no key is configured."""
    result = await screenshot_analyzer._call_vision(
        screenshot_analyzer.CLASSIFY_PROMPT,
        _solid_png_data_url(),
        screenshot_analyzer.ScreenshotClassificationModel,
    )

    assert isinstance(result, screenshot_analyzer.ScreenshotClassificationModel)
    assert result.screenshot_type in set(typing.get_args(screenshot_analyzer.ScreenshotType))
    assert 0.0 <= result.confidence <= 1.0
