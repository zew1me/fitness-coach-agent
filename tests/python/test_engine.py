from datetime import date
from pathlib import Path

import pytest

from backend.engine.gpx_parser import (
    ParsedActivity,
    ParsedCourse,
    _canonical_sport,
    parse_gpx,
    parse_tcx,
)
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

    assert isinstance(activity, ParsedActivity)

    assert activity.rr_intervals_ms == [820, 830, 815, 825]
    assert activity.hrv_summary is not None
    assert activity.hrv_summary["quality"] == "insufficient_rr_intervals"


def test_parse_gpx_trackpoint_extension_values_are_not_duplicated(tmp_path: Path) -> None:
    gpx_file = tmp_path / "run.gpx"
    gpx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test"
     xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0">
      <ele>10</ele><time>2026-04-19T10:00:00Z</time>
      <extensions>
        <gpxtpx:TrackPointExtension>
          <gpxtpx:hr>140</gpxtpx:hr>
          <gpxtpx:cad>85</gpxtpx:cad>
          <gpxtpx:rr>820</gpxtpx:rr>
        </gpxtpx:TrackPointExtension>
      </extensions>
    </trkpt>
    <trkpt lat="37.0" lon="-122.001">
      <ele>12</ele><time>2026-04-19T10:01:00Z</time>
    </trkpt>
  </trkseg></trk>
</gpx>""",
        encoding="utf-8",
    )

    activity = parse_gpx(gpx_file)

    # The second, bare trackpoint exists only to give the file a positive elapsed
    # time so it is classified as a recording. It carries no extensions, so the
    # values below still come from exactly one point and the duplication this test
    # guards against would still show up.
    assert isinstance(activity, ParsedActivity)
    assert activity.avg_hr_bpm == 140
    assert activity.max_hr_bpm == 140
    assert activity.avg_cadence_rpm == 85
    assert activity.rr_intervals_ms == [820]


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

    assert isinstance(activity, ParsedActivity)

    assert activity.sport == "running"
    assert activity.duration_seconds == 60
    assert activity.distance_meters == 200
    assert activity.avg_hr_bpm == 142
    assert activity.max_hr_bpm == 145
    assert activity.rr_intervals_ms == [820, 830, 815, 825]


# --- Course files (planned routes) --------------------------------------------
#
# A course is a route the athlete intends to ride or run, uploaded so the coach can
# advise on it. It is not something they have done, so it must never come back as a
# ParsedActivity. See backend/engine/gpx_parser.py for the per-format evidence each
# file type offers.


def _write_gpx(tmp_path: Path, body: str, name: str = "course.gpx") -> Path:
    gpx_file = tmp_path / name
    gpx_file.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"{body}\n</gpx>",
        encoding="utf-8",
    )
    return gpx_file


# Two points 0.001 degrees of longitude apart at 37 degrees latitude. The closed
# form 111_320 * cos(37 degrees) * 0.001 gives ~88.9 m, which is the independent
# check that distance is real rather than whatever gpxpy happens to return.
_EXPECTED_STEP_METERS = 88.9


def test_parse_gpx_untimed_track_is_a_course_not_a_run(tmp_path: Path) -> None:
    # The reported bug: no <time> means pace inference cannot run, and the old
    # fallback labelled every such file "running" *and* saved it as a completed
    # activity. A course GPX has no timestamps by definition.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>100</ele></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>110</ele></trkpt>
  </trkseg></trk>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.sport == "general"
    assert course.profile.distance_meters == pytest.approx(_EXPECTED_STEP_METERS, abs=1.0)
    assert course.profile.elevation_gain_meters == 10.0


def test_parse_gpx_course_takes_sport_from_declared_track_type(tmp_path: Path) -> None:
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><type>cycling</type><name>Schotterfest Long</name><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>100</ele></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>110</ele></trkpt>
  </trkseg></trk>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.sport == "cycling"
    assert course.name == "Schotterfest Long"


def test_parse_gpx_route_only_file_reports_distance_and_elevation(tmp_path: Path) -> None:
    # <rte>/<rtept> is what Garmin Course and RideWithGPS exports commonly emit.
    # Walking only <trk> reported these as zero distance and zero vertical.
    gpx_file = _write_gpx(
        tmp_path,
        """  <rte><type>Ride</type>
    <rtept lat="37.0" lon="-122.0"><ele>100</ele></rtept>
    <rtept lat="37.0" lon="-122.001"><ele>130</ele></rtept>
  </rte>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.sport == "cycling"
    assert course.profile.distance_meters == pytest.approx(_EXPECTED_STEP_METERS, abs=1.0)
    assert course.profile.elevation_gain_meters == 30.0


def test_parse_gpx_does_not_double_count_a_track_and_its_redundant_route(
    tmp_path: Path,
) -> None:
    # A Garmin course export can carry both. Summing them would double every number.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>100</ele></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>110</ele></trkpt>
  </trkseg></trk>
  <rte>
    <rtept lat="37.0" lon="-122.0"><ele>100</ele></rtept>
    <rtept lat="37.0" lon="-122.001"><ele>110</ele></rtept>
  </rte>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == pytest.approx(_EXPECTED_STEP_METERS, abs=1.0)
    assert course.profile.elevation_gain_meters == 10.0


def test_parse_gpx_reads_declared_sport_from_a_route_beside_an_untyped_track(
    tmp_path: Path,
) -> None:
    # The route's points are skipped, but its <type> is still the only sport signal
    # in the file.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>100</ele></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>110</ele></trkpt>
  </trkseg></trk>
  <rte><type>mountainbiking</type>
    <rtept lat="37.0" lon="-122.0"><ele>100</ele></rtept>
  </rte>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.sport == "cycling"


def test_parse_gpx_unmappable_declared_type_falls_through_to_unknown(tmp_path: Path) -> None:
    # Strava writes a numeric <type>. It must not poison the result.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><type>1</type><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>100</ele></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>110</ele></trkpt>
  </trkseg></trk>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.sport == "general"


def test_parse_gpx_timed_track_is_still_a_recorded_run(tmp_path: Path) -> None:
    # No regression: a real recording keeps its existing pace-inferred sport.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:01:00Z</time></trkpt>
  </trkseg></trk>""",
        name="run.gpx",
    )

    activity = parse_gpx(gpx_file)

    assert isinstance(activity, ParsedActivity)

    assert activity.sport == "running"


def test_parse_gpx_timed_track_at_cycling_pace_is_still_a_recorded_ride(tmp_path: Path) -> None:
    # ~89 m in 10 s is roughly 32 km/h, comfortably under the 180 s/km cycling cutoff.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:00:10Z</time></trkpt>
  </trkseg></trk>""",
        name="ride.gpx",
    )

    activity = parse_gpx(gpx_file)

    assert isinstance(activity, ParsedActivity)

    assert activity.sport == "cycling"


def test_parse_gpx_declared_type_beats_the_pace_heuristic(tmp_path: Path) -> None:
    # A stop-heavy recorded ride averages slower than the cycling cutoff, so pace
    # alone would call it a run. The file says otherwise.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><type>Ride</type><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:30:00Z</time></trkpt>
  </trkseg></trk>""",
        name="ride.gpx",
    )

    activity = parse_gpx(gpx_file)

    assert isinstance(activity, ParsedActivity)

    assert activity.sport == "cycling"


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("Trail_Run", "running"),
        ("trail run", "running"),
        ("TrailRun", "running"),
        ("  RIDE  ", "cycling"),
        ("mountain-biking", "cycling"),
        ("Open Water Swim", "swimming"),
        ("Hike", "hiking"),
        ("rowing", "rowing"),
        ("1", None),
        ("kitesurfing", None),
        ("", None),
        (None, None),
    ],
)
def test_canonical_sport_normalizes_known_dialects(declared: object, expected: str | None) -> None:
    assert _canonical_sport(declared) == expected


def test_parse_tcx_course_file_is_a_course(tmp_path: Path) -> None:
    # Before this, parse_tcx raised ValueError on every Garmin Course export because
    # it searched the whole tree for an <Activity> and found none.
    tcx_file = tmp_path / "course.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Courses>
    <Course>
      <Name>Schotterfest Long</Name>
      <Lap><TotalTimeSeconds>10800</TotalTimeSeconds><DistanceMeters>5000</DistanceMeters></Lap>
      <Track>
        <Trackpoint><Time>2026-06-21T10:00:00Z</Time>
          <AltitudeMeters>100</AltitudeMeters><DistanceMeters>0</DistanceMeters></Trackpoint>
        <Trackpoint><Time>2026-06-21T10:01:00Z</Time>
          <AltitudeMeters>150</AltitudeMeters><DistanceMeters>1000</DistanceMeters></Trackpoint>
      </Track>
    </Course>
  </Courses>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    course = parse_tcx(tcx_file)

    assert isinstance(course, ParsedCourse)
    assert course.name == "Schotterfest Long"
    # The Course schema carries no sport attribute, so it stays unknown.
    assert course.sport == "general"
    # The lap declares 5000 m and the trackpoints add up to 1000 m. Asserting 1000
    # is what proves the trackpoint stream wins over the lap fallback rather than
    # the two happening to agree.
    assert course.profile.distance_meters == 1000.0
    assert course.profile.elevation_gain_meters == 50.0


def test_parse_tcx_prefers_an_activity_when_a_file_carries_both_sections(tmp_path: Path) -> None:
    # Precedence guard: a hybrid file must keep behaving exactly as it does today.
    tcx_file = tmp_path / "hybrid.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Biking">
      <Id>2026-04-19T10:00:00Z</Id>
      <Lap StartTime="2026-04-19T10:00:00Z">
        <TotalTimeSeconds>60</TotalTimeSeconds>
        <DistanceMeters>200</DistanceMeters>
      </Lap>
    </Activity>
  </Activities>
  <Courses><Course><Name>Ignored</Name></Course></Courses>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    activity = parse_tcx(tcx_file)

    assert isinstance(activity, ParsedActivity)

    assert activity.sport == "cycling"


def test_parse_tcx_without_activity_or_course_still_raises(tmp_path: Path) -> None:
    tcx_file = tmp_path / "empty.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not contain an Activity"):
        parse_tcx(tcx_file)


def test_parse_gpx_route_only_file_sums_every_route(tmp_path: Path) -> None:
    # RideWithGPS and Garmin often split one course across several <rte> elements.
    # Re-checking the point counter inside the loop let the first route disable all
    # the rest, so a two-leg course reported only its first leg — the same class of
    # underestimate this change exists to fix.
    gpx_file = _write_gpx(
        tmp_path,
        """  <rte>
    <rtept lat="37.0" lon="-122.0"><ele>100</ele></rtept>
    <rtept lat="37.0" lon="-122.01"><ele>150</ele></rtept>
  </rte>
  <rte>
    <rtept lat="37.0" lon="-122.02"><ele>150</ele></rtept>
    <rtept lat="37.0" lon="-122.03"><ele>230</ele></rtept>
  </rte>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    # Each leg spans 0.01 degrees of longitude at latitude 37: 111_320 * cos(37) *
    # 0.01 is about 889 m, so both legs together are about 1778 m. The gap between
    # legs is deliberately not counted, matching how multi-segment tracks behave.
    assert course.profile.distance_meters == pytest.approx(1778.0, abs=20.0)
    assert course.profile.elevation_gain_meters == 130.0


def test_parse_gpx_multi_segment_track_sums_every_segment(tmp_path: Path) -> None:
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk>
    <trkseg>
      <trkpt lat="37.0" lon="-122.0"><ele>100</ele></trkpt>
      <trkpt lat="37.0" lon="-122.01"><ele>150</ele></trkpt>
    </trkseg>
    <trkseg>
      <trkpt lat="37.0" lon="-122.02"><ele>150</ele></trkpt>
      <trkpt lat="37.0" lon="-122.03"><ele>230</ele></trkpt>
    </trkseg>
  </trk>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == pytest.approx(1778.0, abs=20.0)
    assert course.profile.elevation_gain_meters == 130.0


def test_parse_tcx_course_prefers_declared_distance_over_position(tmp_path: Path) -> None:
    # Garmin's own cumulative DistanceMeters is authoritative where present.
    tcx_file = tmp_path / "course.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Courses>
    <Course>
      <Track>
        <Trackpoint>
          <Position><LatitudeDegrees>37.0</LatitudeDegrees>
            <LongitudeDegrees>-122.0</LongitudeDegrees></Position>
          <AltitudeMeters>100</AltitudeMeters><DistanceMeters>0</DistanceMeters>
        </Trackpoint>
        <Trackpoint>
          <Position><LatitudeDegrees>37.0</LatitudeDegrees>
            <LongitudeDegrees>-122.01</LongitudeDegrees></Position>
          <AltitudeMeters>150</AltitudeMeters><DistanceMeters>2000</DistanceMeters>
        </Trackpoint>
      </Track>
    </Course>
  </Courses>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    course = parse_tcx(tcx_file)

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == 2000.0


def test_parse_gpx_synthetic_timestamp_on_one_point_is_still_a_course(tmp_path: Path) -> None:
    # Planning tools stamp a single <time> on the first point. Testing for the
    # absence of *any* timestamp let that through as a recorded activity — no
    # duration, a fabricated sport, and a shot at satisfying a planned workout.
    # The test has to be elapsed time, which a route never has.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><type>cycling</type><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>100</ele><time>2026-06-21T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.01"><ele>150</ele></trkpt>
    <trkpt lat="37.0" lon="-122.02"><ele>140</ele></trkpt>
  </trkseg></trk>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.sport == "cycling"
    assert course.profile.elevation_gain_meters == 50.0


def test_parse_gpx_identical_timestamps_on_every_point_is_still_a_course(
    tmp_path: Path,
) -> None:
    # The worse variant: stamp the same instant on every point and the file looks
    # fully timed, but the elapsed span is zero. Pace inference would fire on a
    # non-zero duration and invent a sport.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>100</ele><time>2026-06-21T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.01"><ele>150</ele><time>2026-06-21T10:00:00Z</time></trkpt>
  </trkseg></trk>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.sport == "general"


def test_parse_gpx_empty_file_is_a_zero_course_not_a_phantom_activity(tmp_path: Path) -> None:
    # An empty or waypoint-only GPX used to become an activity with every metric
    # None, dated today, and get written to the training log.
    gpx_file = _write_gpx(tmp_path, "  <trk><trkseg></trkseg></trk>")

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == 0.0


def test_parse_tcx_course_without_trackpoint_distance_derives_it_from_position(
    tmp_path: Path,
) -> None:
    # DistanceMeters is optional on a TCX Trackpoint. Carrying the previous
    # cumulative value forward made every delta zero, so the course reported "0 m
    # with 50 m of climbing" — coherent-looking and wrong, while the guard that
    # suppresses a pacing estimate still told the coach the totals were accurate.
    tcx_file = tmp_path / "course.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Courses>
    <Course>
      <Name>No Distance Course</Name>
      <Track>
        <Trackpoint>
          <Position>
            <LatitudeDegrees>37.0</LatitudeDegrees><LongitudeDegrees>-122.0</LongitudeDegrees>
          </Position>
          <AltitudeMeters>100</AltitudeMeters>
        </Trackpoint>
        <Trackpoint>
          <Position>
            <LatitudeDegrees>37.0</LatitudeDegrees><LongitudeDegrees>-122.01</LongitudeDegrees>
          </Position>
          <AltitudeMeters>150</AltitudeMeters>
        </Trackpoint>
      </Track>
    </Course>
  </Courses>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    course = parse_tcx(tcx_file)

    assert isinstance(course, ParsedCourse)
    # 0.01 degrees of longitude at latitude 37 is about 889 m.
    assert course.profile.distance_meters == pytest.approx(889.0, abs=20.0)
    assert course.profile.elevation_gain_meters == 50.0


def test_parse_tcx_course_mixing_declared_and_derived_distance_does_not_double_count(
    tmp_path: Path,
) -> None:
    # A position-derived step used to leave the declared-distance baseline behind, so
    # the next DistanceMeters was measured against a stale value and the movement in
    # between was counted twice: a course declaring 178 m came out at 267 m.
    tcx_file = tmp_path / "course.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Courses>
    <Course>
      <Track>
        <Trackpoint>
          <Position><LatitudeDegrees>37.0</LatitudeDegrees>
            <LongitudeDegrees>-122.0</LongitudeDegrees></Position>
          <AltitudeMeters>10</AltitudeMeters><DistanceMeters>0</DistanceMeters>
        </Trackpoint>
        <Trackpoint>
          <Position><LatitudeDegrees>37.0</LatitudeDegrees>
            <LongitudeDegrees>-122.001</LongitudeDegrees></Position>
          <AltitudeMeters>20</AltitudeMeters>
        </Trackpoint>
        <Trackpoint>
          <Position><LatitudeDegrees>37.0</LatitudeDegrees>
            <LongitudeDegrees>-122.002</LongitudeDegrees></Position>
          <AltitudeMeters>30</AltitudeMeters><DistanceMeters>178</DistanceMeters>
        </Trackpoint>
      </Track>
    </Course>
  </Courses>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    course = parse_tcx(tcx_file)

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == pytest.approx(178.0, abs=1.0)


def test_parse_tcx_course_declared_distance_after_derived_steps_re_anchors(
    tmp_path: Path,
) -> None:
    # The mirror of the double-count case: when the declared DistanceMeters arrives
    # last, a declared-only baseline started from scratch and threw away the derived
    # movement, reporting 89 m for a course declaring 178 m. One running total,
    # advanced by every step, gets both orders right.
    tcx_file = tmp_path / "course.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Courses>
    <Course>
      <Track>
        <Trackpoint>
          <Position><LatitudeDegrees>37.0</LatitudeDegrees>
            <LongitudeDegrees>-122.0</LongitudeDegrees></Position>
          <AltitudeMeters>10</AltitudeMeters>
        </Trackpoint>
        <Trackpoint>
          <Position><LatitudeDegrees>37.0</LatitudeDegrees>
            <LongitudeDegrees>-122.001</LongitudeDegrees></Position>
          <AltitudeMeters>20</AltitudeMeters>
        </Trackpoint>
        <Trackpoint>
          <Position><LatitudeDegrees>37.0</LatitudeDegrees>
            <LongitudeDegrees>-122.002</LongitudeDegrees></Position>
          <AltitudeMeters>30</AltitudeMeters><DistanceMeters>178</DistanceMeters>
        </Trackpoint>
      </Track>
    </Course>
  </Courses>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    course = parse_tcx(tcx_file)

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == pytest.approx(178.0, abs=1.0)


def test_parse_tcx_course_falls_back_to_the_lap_distance_total(tmp_path: Path) -> None:
    # Trackpoints with neither DistanceMeters nor Position leave the stream at zero.
    # The Lap total is the only distance the file states, and reporting a coherent
    # "0 m course with 800 m of climbing" instead would be silently wrong.
    tcx_file = tmp_path / "course.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Courses>
    <Course>
      <Name>Lap total only</Name>
      <Lap><DistanceMeters>42195</DistanceMeters></Lap>
      <Track>
        <Trackpoint><AltitudeMeters>100</AltitudeMeters></Trackpoint>
        <Trackpoint><AltitudeMeters>900</AltitudeMeters></Trackpoint>
      </Track>
    </Course>
  </Courses>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    course = parse_tcx(tcx_file)

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == 42195.0
    # Gain stays 0 and the grades stay None on purpose. With no DistanceMeters and no
    # Position, every reading lands at the same cumulative distance, and the guard
    # that stops duplicated points inventing vertical cannot tell that case apart
    # from this one. The lap total is real information and is reported; where the
    # climbing sits along it is not, so nothing is claimed about it.
    assert course.profile.elevation_gain_meters == 0.0
    assert course.profile.avg_grade_pct is None


def test_parse_tcx_course_prefers_trackpoint_distance_over_the_lap_total(
    tmp_path: Path,
) -> None:
    tcx_file = tmp_path / "course.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Courses>
    <Course>
      <Lap><DistanceMeters>99999</DistanceMeters></Lap>
      <Track>
        <Trackpoint>
          <AltitudeMeters>100</AltitudeMeters><DistanceMeters>0</DistanceMeters>
        </Trackpoint>
        <Trackpoint>
          <AltitudeMeters>150</AltitudeMeters><DistanceMeters>1000</DistanceMeters>
        </Trackpoint>
      </Track>
    </Course>
  </Courses>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    course = parse_tcx(tcx_file)

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == 1000.0


def test_parse_gpx_out_of_order_timestamps_are_a_course_not_a_negative_activity(
    tmp_path: Path,
) -> None:
    # A file whose last point predates its first yields a negative elapsed span.
    # Negative is truthy, so it was classified as a recording and reached
    # ParsedActivity with duration_seconds = -300 — and a negative pace reads as
    # faster than the cycling cutoff, so it was labelled a ride too.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-04-19T10:05:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:00:00Z</time></trkpt>
  </trkseg></trk>""",
    )

    course = parse_gpx(gpx_file)

    assert isinstance(course, ParsedCourse)
    assert course.sport == "general"


def test_parse_gpx_partial_elevation_stream_does_not_invent_a_climb(tmp_path: Path) -> None:
    # Recorded-activity path, not the course one. Coalescing a missing <ele> to 0 made
    # the first real reading a climb from sea level: a track whose opening point has
    # no elevation followed by one at 500 m reported 500 m of ascent, which then went
    # into the saved activity.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>500</ele><time>2026-04-19T10:10:00Z</time></trkpt>
  </trkseg></trk>""",
        name="run.gpx",
    )

    activity = parse_gpx(gpx_file)

    assert isinstance(activity, ParsedActivity)
    assert activity.elevation_gain_meters is None


def test_parse_gpx_counts_a_climb_between_two_real_elevations(tmp_path: Path) -> None:
    # The guard must drop only the unpaired reading, not real ascent around it.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>100</ele><time>2026-04-19T10:05:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.002"><ele>180</ele><time>2026-04-19T10:10:00Z</time></trkpt>
  </trkseg></trk>""",
        name="run.gpx",
    )

    activity = parse_gpx(gpx_file)

    assert isinstance(activity, ParsedActivity)
    assert activity.elevation_gain_meters == 80.0


def test_parse_tcx_course_does_not_take_its_name_from_a_waypoint(tmp_path: Path) -> None:
    # A <Course> with no <Name> of its own but with <CoursePoint> waypoints: searching
    # descendants picked up the first waypoint's label, so the coach told the athlete
    # their route was called "Water stop".
    tcx_file = tmp_path / "course.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Courses>
    <Course>
      <Track>
        <Trackpoint>
          <AltitudeMeters>100</AltitudeMeters><DistanceMeters>0</DistanceMeters></Trackpoint>
        <Trackpoint>
          <AltitudeMeters>150</AltitudeMeters><DistanceMeters>1000</DistanceMeters></Trackpoint>
      </Track>
      <CoursePoint><Name>Water stop</Name><PointType>Food</PointType></CoursePoint>
    </Course>
  </Courses>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    course = parse_tcx(tcx_file)

    assert isinstance(course, ParsedCourse)
    assert course.name is None
    # The terrain is unaffected by the naming fix.
    assert course.profile.distance_meters == 1000.0


def test_parse_tcx_course_opening_part_way_along_its_route(tmp_path: Path) -> None:
    # A course exported from part-way along a longer route opens at a non-zero
    # cumulative DistanceMeters. Charging that opening reading as a step turned a
    # 1 km course into a 6 km one — the same defect already fixed on the FIT path.
    tcx_file = tmp_path / "course.tcx"
    tcx_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Courses>
    <Course>
      <Track>
        <Trackpoint>
          <AltitudeMeters>100</AltitudeMeters><DistanceMeters>5000</DistanceMeters></Trackpoint>
        <Trackpoint>
          <AltitudeMeters>150</AltitudeMeters><DistanceMeters>6000</DistanceMeters></Trackpoint>
      </Track>
    </Course>
  </Courses>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )

    course = parse_tcx(tcx_file)

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == 1000.0
    assert course.profile.elevation_gain_meters == 50.0


def test_parse_gpx_recording_whose_first_point_lost_its_timestamp(tmp_path: Path) -> None:
    # A unit that drops the timestamp on its opening fix used to leave start_time
    # unset, so the elapsed span was None and a genuine ride was classified as a
    # course — meaning it was never logged at all. The scan now starts at the first
    # point that actually carries a time.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:01:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.002"><ele>14</ele><time>2026-04-19T10:02:00Z</time></trkpt>
  </trkseg></trk>""",
        name="run.gpx",
    )

    activity = parse_gpx(gpx_file)

    assert isinstance(activity, ParsedActivity)
    assert activity.duration_seconds == 60


def test_parse_gpx_recording_whose_last_point_lost_its_timestamp(tmp_path: Path) -> None:
    # The mirror case: a trailing point with no time must not blank out end_time.
    gpx_file = _write_gpx(
        tmp_path,
        """  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:01:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.002"><ele>14</ele></trkpt>
  </trkseg></trk>""",
        name="run.gpx",
    )

    activity = parse_gpx(gpx_file)

    assert isinstance(activity, ParsedActivity)
    assert activity.duration_seconds == 60
