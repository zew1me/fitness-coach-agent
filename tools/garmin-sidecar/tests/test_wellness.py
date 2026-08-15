from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from fitness_coach_garmin_sidecar import cli as garmin_cli
from fitness_coach_garmin_sidecar import wellness
from typer.testing import CliRunner


class FakeWellnessClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_sleep_data(self, cdate: str) -> dict[str, Any]:
        self.calls.append(("sleep", cdate))
        if cdate == "2026-08-15":
            return {
                "dailySleepDTO": {
                    "sleepTimeSeconds": 26640,
                    "sleepScores": {
                        "overall": {"value": 82},
                        "sleepConsistency": 78,
                    },
                }
            }
        return {
            "dailySleepDTO": {
                "sleepTimeSeconds": 0,
                "sleepScores": {"overall": {"value": 0}},
            }
        }

    def get_hrv_data(self, cdate: str) -> dict[str, Any] | None:
        self.calls.append(("hrv", cdate))
        if cdate == "2026-08-14":
            raise RuntimeError("HRV unavailable")
        return {"hrvSummary": {"lastNightAvg": 44.0}}

    def get_body_battery(self, startdate: str, enddate: str | None = None) -> list[dict[str, Any]]:
        self.calls.append(("body_battery", startdate))
        if startdate == "2026-08-15":
            return [
                {
                    "date": startdate,
                    "bodyBatteryValuesArray": [[1, 0], [2, None], [3, 78]],
                }
            ]
        return []

    def get_rhr_day(self, cdate: str) -> dict[str, Any]:
        self.calls.append(("resting_hr", cdate))
        readings = []
        if cdate == "2026-08-15":
            readings = [{"calendarDate": cdate, "value": 49}]
        return {"allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": readings}}}

    def get_user_summary(self, cdate: str) -> dict[str, Any]:
        self.calls.append(("daily_summary", cdate))
        if cdate == "2026-08-15":
            return {
                "calendarDate": cdate,
                "averageStressLevel": 28,
                "restingHeartRate": 99,
            }
        return {
            "calendarDate": cdate,
            "averageStressLevel": 0,
            "restingHeartRate": 54,
        }


def test_collect_wellness_emits_supported_fields_newest_first_and_omits_missing() -> None:
    client = FakeWellnessClient()

    summary = wellness.collect_wellness(
        client,
        start=date(2026, 8, 14),
        end=date(2026, 8, 15),
    )

    assert summary.rows == [
        {
            "log_date": "2026-08-15",
            "sleep_duration_hours": 7.4,
            "sleep_score": 82,
            "sleep_consistency_pct": 78,
            "hrv_ms": 44.0,
            "resting_hr_bpm": 49,
            "body_battery": 78,
            "stress_score": 28,
        },
        {
            "log_date": "2026-08-14",
            "sleep_duration_hours": 0.0,
            "sleep_score": 0,
            "resting_hr_bpm": 54,
            "stress_score": 0,
        },
    ]
    assert [
        (failure.log_date, failure.metric, failure.message) for failure in summary.failures
    ] == [(date(2026, 8, 14), "hrv", "HRV unavailable")]
    assert client.calls == [
        (metric, log_date)
        for log_date in ("2026-08-15", "2026-08-14")
        for metric in ("sleep", "hrv", "body_battery", "resting_hr", "daily_summary")
    ]


def test_collect_wellness_does_not_use_metrics_returned_for_a_different_date() -> None:
    class WrongDateClient(FakeWellnessClient):
        def get_body_battery(
            self, startdate: str, enddate: str | None = None
        ) -> list[dict[str, Any]]:
            return [
                {
                    "date": "2026-08-14",
                    "bodyBatteryValuesArray": [[1, 91]],
                }
            ]

        def get_rhr_day(self, cdate: str) -> dict[str, Any]:
            return {
                "allMetrics": {
                    "metricsMap": {
                        "WELLNESS_RESTING_HEART_RATE": [{"calendarDate": "2026-08-14", "value": 48}]
                    }
                }
            }

        def get_user_summary(self, cdate: str) -> dict[str, Any]:
            return {
                "calendarDate": "2026-08-14",
                "averageStressLevel": 28,
                "restingHeartRate": 47,
            }

    summary = wellness.collect_wellness(
        WrongDateClient(),
        start=date(2026, 8, 15),
        end=date(2026, 8, 15),
    )

    assert "body_battery" not in summary.rows[0]
    assert "resting_hr_bpm" not in summary.rows[0]
    assert "stress_score" not in summary.rows[0]


def test_text_and_json_formatters_emit_the_same_rows() -> None:
    rows: list[wellness.WellnessRow] = [
        {
            "log_date": "2026-08-15",
            "sleep_duration_hours": 7.4,
            "body_battery": 0,
        }
    ]

    text = wellness.format_wellness_text(
        rows,
        start=date(2026, 8, 9),
        end=date(2026, 8, 15),
        timezone_name="US/Pacific",
    )

    assert text == (
        "=== WELLNESS EXPORT v1 source=garmin_sidecar ===\n"
        "dates: 2026-08-09..2026-08-15  timezone: US/Pacific\n"
        "log_date=2026-08-15 sleep_duration_hours=7.4 body_battery=0\n"
        "=== END WELLNESS EXPORT ==="
    )
    assert json.loads(wellness.format_wellness_json(rows)) == rows


def test_wellness_cli_reports_partial_failure_after_printing_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = wellness.WellnessExportSummary(
        rows=[{"log_date": "2026-08-15", "hrv_ms": 44}],
        failures=[
            wellness.WellnessFailure(
                log_date=date(2026, 8, 14),
                metric="sleep",
                message="sleep endpoint unavailable",
            )
        ],
    )
    monkeypatch.setattr(garmin_cli, "authenticate", lambda **_kwargs: object())
    monkeypatch.setattr(garmin_cli, "collect_wellness", lambda *_args, **_kwargs: summary)
    monkeypatch.setattr(garmin_cli, "local_timezone_name", lambda: "US/Pacific")

    result = CliRunner().invoke(
        garmin_cli.app,
        ["wellness", "export", "2026-08-09", "2026-08-15"],
    )

    assert result.exit_code == 1
    assert result.stdout.startswith("=== WELLNESS EXPORT v1 source=garmin_sidecar ===")
    assert "log_date=2026-08-15 hrv_ms=44" in result.stdout
    assert "failed 2026-08-14 sleep: sleep endpoint unavailable" in result.stderr
    assert "Summary: 1 days exported, 1 metric requests failed across 1 days." in result.stderr


def test_wellness_json_is_machine_readable_when_a_metric_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = wellness.WellnessExportSummary(
        rows=[{"log_date": "2026-08-15", "body_battery": 0}],
        failures=[
            wellness.WellnessFailure(
                log_date=date(2026, 8, 15),
                metric="hrv",
                message="missing",
            )
        ],
    )
    monkeypatch.setattr(garmin_cli, "authenticate", lambda **_kwargs: object())
    monkeypatch.setattr(garmin_cli, "collect_wellness", lambda *_args, **_kwargs: summary)

    result = CliRunner().invoke(
        garmin_cli.app,
        ["wellness", "export", "2026-08-15", "2026-08-15", "--format", "json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout) == summary.rows
    assert "failed 2026-08-15 hrv: missing" in result.stderr


def test_wellness_cli_validates_format_before_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_authentication(**_kwargs: Any) -> None:
        raise AssertionError("authentication must not run")

    monkeypatch.setattr(garmin_cli, "authenticate", unexpected_authentication)

    result = CliRunner().invoke(
        garmin_cli.app,
        ["wellness", "export", "2026-08-15", "2026-08-15", "--format", "yaml"],
    )

    assert result.exit_code == 2
    assert "format must be 'text' or 'json'" in result.output
