"""Course terrain math, checked against hand-computed answers.

Every fixture here has an arithmetically known result so the assertions are
independent of the implementation rather than a snapshot of it.
"""

from __future__ import annotations

import time

from backend.engine.course_profile import (
    MAX_GRADE_WINDOW_METERS,
    MIN_GRADE_WINDOW_METERS,
    CoursePoint,
    build_course_points,
    summarize_course,
    summarize_course_with_totals,
)


def _points(*pairs: tuple[float, float | None]) -> list[CoursePoint]:
    """Build points from explicit ``(cumulative_distance, elevation)`` pairs."""
    return [CoursePoint(distance, elevation) for distance, elevation in pairs]


def test_steady_climb_reports_its_actual_grade() -> None:
    # 1000 m out, 50 m up, sampled every 100 m: a dead-flat 5% the whole way.
    points = _points(*((distance, distance * 0.05) for distance in range(0, 1001, 100)))

    profile = summarize_course(points)

    assert profile.distance_meters == 1000.0
    assert profile.elevation_gain_meters == 50.0
    assert profile.avg_grade_pct == 5.0
    assert profile.max_grade_pct == 5.0


def test_avg_grade_uses_ascending_distance_not_total_distance() -> None:
    # Up 50 m over 500 m (10%), back down over 500 m. Net grade is 0, but the
    # climbing the athlete actually does averages 10%.
    points = _points((0, 0), (500, 50), (1000, 0))

    profile = summarize_course(points)

    assert profile.distance_meters == 1000.0
    assert profile.elevation_gain_meters == 50.0
    assert profile.avg_grade_pct == 10.0


def test_rolling_profile_sums_every_ascent() -> None:
    # Two 30 m climbs separated by a descent: 60 m of gain, not the 10 m net.
    points = _points((0, 100), (400, 130), (800, 120), (1200, 150))

    profile = summarize_course(points)

    assert profile.elevation_gain_meters == 60.0
    # Ascending distance is 400 + 400 = 800 m for 60 m of gain.
    assert profile.avg_grade_pct == 7.5


def test_single_point_elevation_spike_is_not_reported_as_max_grade() -> None:
    # A 15 m GPS/barometric spike between two points 3 m apart is a 500% "grade".
    # The window minimum has to swallow it.
    points = _points((0, 100), (3, 115), (6, 100), (500, 110))

    profile = summarize_course(points)

    assert profile.max_grade_pct is not None
    assert profile.max_grade_pct < 10.0


def test_max_grade_finds_a_pitch_longer_than_the_shortest_valid_window() -> None:
    # Regression: anchoring on the *shortest* window at or above the minimum reports
    # 0% here, because the first valid window (0 m -> 50 m) is flat and no window
    # starts at 50 m. The real answer is the 0 m -> 60 m pitch.
    points = _points((0, 0), (MIN_GRADE_WINDOW_METERS, 0), (60, 10))

    profile = summarize_course(points)

    assert profile.max_grade_pct == round(10 / 60 * 100, 1)


def test_max_grade_ignores_windows_longer_than_the_ceiling() -> None:
    # A gentle 2% ramp far beyond the ceiling, then a genuine 12% pitch inside it.
    long_span = MAX_GRADE_WINDOW_METERS * 4
    ramp_top = long_span * 0.02
    points = _points((0, 0), (long_span, ramp_top), (long_span + 100, ramp_top + 12))

    profile = summarize_course(points)

    assert profile.max_grade_pct == 12.0


def test_coarsely_sampled_course_still_gets_a_max_grade() -> None:
    # Every window here is far past the ceiling. Treating the ceiling as a hard
    # filter would report no max grade at all; it is a stopping rule instead, so the
    # first window at or above the minimum still counts.
    points = _points((0, 0), (1000, 80), (2000, 80))

    profile = summarize_course(points)

    assert profile.max_grade_pct == 8.0


def test_course_without_elevation_reports_distance_but_no_grades() -> None:
    points = _points((0, None), (500, None), (1000, None))

    profile = summarize_course(points)

    assert profile.distance_meters == 1000.0
    assert profile.elevation_gain_meters == 0.0
    assert profile.avg_grade_pct is None
    assert profile.max_grade_pct is None


def test_flat_course_with_elevation_reports_zero_grade_not_none() -> None:
    # Distinct from the no-elevation case: we know the grade, and it is zero.
    points = _points((0, 10), (500, 10), (1000, 10))

    profile = summarize_course(points)

    assert profile.elevation_gain_meters == 0.0
    assert profile.avg_grade_pct is None  # no ascending distance to average over
    assert profile.max_grade_pct == 0.0


def test_empty_course_is_all_zeroes_and_nones() -> None:
    profile = summarize_course([])

    assert profile.distance_meters == 0.0
    assert profile.elevation_gain_meters == 0.0
    assert profile.avg_grade_pct is None
    assert profile.max_grade_pct is None


def test_build_course_points_accumulates_segment_distances() -> None:
    points = build_course_points([(0.0, 10.0), (100.0, 15.0), (150.0, 12.0)])

    assert [point.cumulative_distance_meters for point in points] == [0.0, 100.0, 250.0]
    assert [point.elevation_meters for point in points] == [10.0, 15.0, 12.0]


def test_build_course_points_clamps_negative_segment_distances() -> None:
    # gpxpy can hand back a negative or nonsensical delta on a malformed file;
    # cumulative distance must never walk backwards.
    points = build_course_points([(0.0, 10.0), (-50.0, 11.0), (100.0, 12.0)])

    assert [point.cumulative_distance_meters for point in points] == [0.0, 0.0, 100.0]


def test_supplied_totals_win_over_the_point_stream() -> None:
    # A FIT course: the lap message is authoritative for distance and vertical,
    # while the record stream exists only to derive grades.
    points = _points((0, 0), (500, 50), (1000, 40))

    profile = summarize_course_with_totals(
        points, distance_meters=94490.2, elevation_gain_meters=2308.6
    )

    assert profile.distance_meters == 94490.2
    assert profile.elevation_gain_meters == 2308.6
    # Grades still come from the points: 50 m of gain over 500 m of ascent.
    assert profile.avg_grade_pct == 10.0


def test_missing_totals_fall_back_to_the_point_stream() -> None:
    points = _points((0, 0), (500, 50))

    profile = summarize_course_with_totals(points, distance_meters=None, elevation_gain_meters=None)

    assert profile.distance_meters == 500.0
    assert profile.elevation_gain_meters == 50.0


def test_supplied_totals_without_any_points_omit_grades() -> None:
    # The FIT-course-with-no-record-stream case: totals are real, grades are unknown,
    # and unknown must surface as None rather than a fabricated 0.0.
    profile = summarize_course_with_totals(
        [], distance_meters=94490.2, elevation_gain_meters=2308.6
    )

    assert profile.distance_meters == 94490.2
    assert profile.elevation_gain_meters == 2308.6
    assert profile.avg_grade_pct is None
    assert profile.max_grade_pct is None


def test_duplicate_location_points_do_not_make_the_grade_scan_quadratic() -> None:
    # A duplicated or paused export puts thousands of points at one location. Every
    # span is zero, so a scan that walks the remaining tail looking for a window at
    # or above the minimum does it once per origin. At 50k points that was tens of
    # seconds; the monotonic window pointer makes it linear. The ceiling here is
    # deliberately loose — it is a guard against reintroducing quadratic behaviour,
    # not a benchmark.
    points = [CoursePoint(0.0, 100.0) for _ in range(50_000)]

    started = time.perf_counter()
    profile = summarize_course(points)
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0
    # No window ever reaches the minimum, so there is no grade to report.
    assert profile.max_grade_pct is None


def test_dense_sampling_finds_the_same_grade_as_sparse_sampling() -> None:
    # The window pointer must not skip candidates. A 10% climb sampled every metre
    # has to report the same 10% as one sampled every 100 m.
    dense = _points(*((float(d), d * 0.1) for d in range(0, 501)))
    sparse = _points(*((float(d), d * 0.1) for d in range(0, 501, 100)))

    assert summarize_course(dense).max_grade_pct == summarize_course(sparse).max_grade_pct == 10.0


def test_two_point_clusters_do_not_make_the_grade_scan_quadratic() -> None:
    # Harder than a single cluster: the window pointer jumps past the first group
    # immediately and the inner loop breaks on its first candidate, yet slicing the
    # remaining tail on every origin was still quadratic in memory traffic.
    points = [CoursePoint(0.0, 100.0) for _ in range(25_000)]
    points += [CoursePoint(1000.0, 200.0) for _ in range(25_000)]

    started = time.perf_counter()
    profile = summarize_course(points)
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0
    # 100 m of rise over 1000 m, and the answer must survive the optimisation.
    assert profile.max_grade_pct == 10.0


def test_elevation_change_over_zero_distance_is_not_counted_as_gain() -> None:
    # Duplicated points with differing elevations are a vertical teleport — GPS or
    # barometric noise, or a backwards jump that build_course_points clamped to zero.
    # Counting the rise inflates the vertical while adding nothing to the distance it
    # is averaged over.
    points = _points((0, 0), (0, 100), (100, 100))

    profile = summarize_course(points)

    assert profile.elevation_gain_meters == 0.0
    assert profile.avg_grade_pct is None


def test_a_real_climb_after_a_duplicated_point_is_still_counted() -> None:
    # The guard must drop only the zero-length interval, not the climb around it.
    points = _points((0, 0), (0, 50), (200, 70))

    profile = summarize_course(points)

    assert profile.elevation_gain_meters == 20.0
    assert profile.avg_grade_pct == 10.0
