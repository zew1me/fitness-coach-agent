import json

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
