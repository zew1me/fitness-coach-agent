import json
from typing import cast

import httpx
import pytest

from backend.engine import screenshot_analyzer


@pytest.mark.asyncio
async def test_extract_training_load_chart_to_series(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    chart_data = {
        "date_range": {"end": "2026-04-26", "start": "2026-04-20"},
        "series": [
            {"date": "2026-04-20", "metric": "ctl", "value": 42},
            {"date": "2026-04-20", "metric": "atl", "value": 50},
            {"date": "2026-04-20", "metric": "tsb", "value": -8},
        ],
        "source_app_hint": "intervals.icu",
    }

    async def fake_call_vision(prompt: str, image_url: str) -> str:
        calls.append((prompt, image_url))
        return json.dumps(chart_data)

    monkeypatch.setattr(screenshot_analyzer, "_call_vision", fake_call_vision)

    result = await screenshot_analyzer.extract_from_screenshot(
        "https://example.com/chart.png",
        "training_load_chart",
    )

    assert result.screenshot_type == "training_load_chart"
    assert result.data == chart_data
    assert calls == [
        (
            screenshot_analyzer.EXTRACT_TRAINING_LOAD_CHART_PROMPT,
            "https://example.com/chart.png",
        )
    ]


@pytest.mark.asyncio
async def test_analyze_screenshot_returns_unknown_when_vision_rejects_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    class FakeAsyncClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            requests.append(kwargs)
            request = httpx.Request("POST", url)
            return httpx.Response(
                400,
                json={"error": {"message": "invalid image URL"}},
                request=request,
            )

    monkeypatch.setattr(screenshot_analyzer.settings, "openai_api_key", "openai-key")
    monkeypatch.setattr(screenshot_analyzer.httpx, "AsyncClient", FakeAsyncClient)

    result = await screenshot_analyzer.analyze_screenshot("https://example.com/private.png")

    assert result.screenshot_type == "unknown"
    assert result.raw_response == "Could not confidently classify this screenshot."
    assert result.data["classification"]["confidence"] == 0.0
    request_json = cast(dict[str, object], requests[0]["json"])
    assert request_json["model"] == "gpt-5.4-mini"
