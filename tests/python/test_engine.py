from datetime import date
from pathlib import Path

from backend.engine.gpx_parser import parse_gpx, parse_tcx
from backend.engine.hrv import summarize_hrv
from backend.engine.periodization import build_plan_skeleton
from backend.engine.thresholds import estimate_cycling_thresholds, estimate_running_thresholds
from backend.engine.training_load import recompute_load_series
from backend.engine.tss import compute_tss
from backend.engine.zones import cycling_power_zones, running_pace_zones


def test_cycling_power_zones_use_ftp_and_lt1_boundary() -> None:
    zones = cycling_power_zones(ftp=300)

    assert zones[0].power_high == 165
    assert zones[1].power_low == 166
    assert zones[1].power_high == 225
    assert zones[3].name == "Threshold"


def test_running_thresholds_convert_10k_to_vdot_paces() -> None:
    thresholds = estimate_running_thresholds(
        race_time_seconds=42 * 60,
        race_distance_meters=10_000,
    )
    zones = running_pace_zones(thresholds.lt2_pace_sec_km, thresholds.lt1_pace_sec_km)

    assert thresholds.vdot >= 40
    assert thresholds.lt1_pace_sec_km > thresholds.lt2_pace_sec_km
    assert zones[-1].name == "VO2max"


def test_cycling_thresholds_estimate_ftp_from_twenty_minute_power() -> None:
    thresholds = estimate_cycling_thresholds(test_power_watts=300, test_duration_minutes=20)

    assert thresholds.ftp_watts == 285
    assert thresholds.lt1_watts == 214


def test_compute_tss_prefers_power_when_available() -> None:
    tss = compute_tss(
        3600,
        sport="cycling",
        normalized_power=250,
        ftp=250,
        avg_hr=120,
        resting_hr=50,
        max_hr=180,
    )

    assert tss == 100


def test_recompute_load_series_applies_ctl_atl_formulas() -> None:
    snapshots = recompute_load_series(
        {date(2026, 4, 1): 100, date(2026, 4, 2): 50},
        date(2026, 4, 1),
        date(2026, 4, 2),
    )

    assert len(snapshots) == 2
    assert snapshots[0]["daily_tss"] == 100
    assert snapshots[0]["ctl"] == round(100 / 42, 1)
    assert snapshots[0]["atl"] == round(100 / 7, 1)
    assert snapshots[1]["tsb"] == round(snapshots[1]["ctl"] - snapshots[1]["atl"], 1)


def test_plan_skeleton_works_backward_from_event_date() -> None:
    skeleton = build_plan_skeleton(
        current_ctl=40,
        target_date=date(2026, 7, 1),
        available_hours_per_week=8,
        start_date=date(2026, 4, 1),
    )

    assert skeleton.total_weeks == 13
    assert skeleton.phases[0].name == "Base"
    assert skeleton.phases[-1].name == "Taper"
    assert skeleton.starting_weekly_tss == 280


def test_hrv_summary_reports_basic_metrics_and_dfa_alpha() -> None:
    intervals = [820, 830, 815, 825, 840, 810, 835, 845] * 20

    summary = summarize_hrv(intervals)

    assert summary["sample_count"] == 160
    assert summary["quality"] == "usable"
    assert summary["rmssd_ms"] is not None
    assert summary["rmssd_ms"] > 0
    assert summary["sdnn_ms"] is not None
    assert summary["sdnn_ms"] > 0
    assert summary["dfa_alpha1"] is not None


def test_parse_gpx_extracts_rr_interval_extensions(tmp_path: Path) -> None:
    gpx_file = tmp_path / "run.gpx"
    gpx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0">
      <ele>10</ele><time>2026-04-19T10:00:00Z</time>
      <extensions><rr>820</rr><rr>830</rr></extensions>
    </trkpt>
    <trkpt lat="37.0" lon="-122.001">
      <ele>12</ele><time>2026-04-19T10:01:00Z</time>
      <extensions><rr>815</rr><rr>825</rr></extensions>
    </trkpt>
  </trkseg></trk>
</gpx>""",
        encoding="utf-8",
    )

    activity = parse_gpx(gpx_file)

    assert activity.rr_intervals_ms == [820, 830, 815, 825]
    assert activity.hrv_summary is not None
    assert activity.hrv_summary["quality"] == "insufficient_rr_intervals"


def test_parse_tcx_extracts_activity_and_rr_intervals(tmp_path: Path) -> None:
    tcx_file = tmp_path / "run.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Running">
      <Id>2026-04-19T10:00:00Z</Id>
      <Lap StartTime="2026-04-19T10:00:00Z">
        <TotalTimeSeconds>60</TotalTimeSeconds>
        <DistanceMeters>200</DistanceMeters>
        <Track>
          <Trackpoint>
            <Time>2026-04-19T10:00:00Z</Time>
            <DistanceMeters>0</DistanceMeters>
            <HeartRateBpm><Value>140</Value></HeartRateBpm>
            <Extensions><rr>820</rr><rr>830</rr></Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-04-19T10:01:00Z</Time>
            <DistanceMeters>200</DistanceMeters>
            <HeartRateBpm><Value>145</Value></HeartRateBpm>
            <Extensions><rr>815</rr><rr>825</rr></Extensions>
          </Trackpoint>
        </Track>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    activity = parse_tcx(tcx_file)

    assert activity.sport == "running"
    assert activity.duration_seconds == 60
    assert activity.distance_meters == 200
    assert activity.avg_hr_bpm == 142
    assert activity.max_hr_bpm == 145
    assert activity.rr_intervals_ms == [820, 830, 815, 825]
