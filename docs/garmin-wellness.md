# Garmin wellness export

The Garmin sidecar can turn daily watch signals into a text block that the fitness coach can save as
recovery data. This is a local, copy-and-paste workflow: the command reads Garmin Connect, prints to
your terminal, and writes nothing to the fitness coach server or to disk.

Garmin Connect is not a supported public API. Garmin may change or block these calls without
notice. See [the Garmin sidecar guide](garmin-sidecar.md) for installation, authentication, and token
storage details.

## Generate an export

From the repository root, run:

```bash
uv run --package fitness-coach-garmin-sidecar garmin-sidecar wellness export \
  2026-08-09 2026-08-15
```

The start and end dates are inclusive and must use `YYYY-MM-DD`. Rows are printed newest first:

```text
=== WELLNESS EXPORT v1 source=garmin_sidecar ===
dates: 2026-08-09..2026-08-15  timezone: America/Los_Angeles
log_date=2026-08-15 sleep_duration_hours=7.4 sleep_score=82 sleep_consistency_pct=78 hrv_ms=44 resting_hr_bpm=49 body_battery=78 stress_score=28
log_date=2026-08-14 sleep_duration_hours=5.9 sleep_score=61 hrv_ms=31 resting_hr_bpm=54 body_battery=52 stress_score=41
=== END WELLNESS EXPORT ===
```

The timezone label comes from the computer running the sidecar (the `TZ` environment variable when
set, otherwise the operating-system timezone). Dates sent to Garmin and returned as `log_date` are
the dates entered on the command line; the sidecar does not shift them between timezones.

Copy the entire block, including both `===` marker lines, and paste it into the web chat. The marker
identifies the content as wellness data so the coach routes the rows to `save_recovery_data`, not to
activity ingestion. Review the values before pasting and tell the coach about any context Garmin
cannot know, such as illness or unusual travel.

## Fields and Garmin origins

The export includes only fields already supported by the coach's recovery log. A missing Garmin
value is omitted from that day's line; it is never rendered as `null` or changed to zero. A measured
zero, including body battery zero, is retained.

| Export field            | Garmin call and value                                                                      | Meaning                                                           |
| ----------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| `log_date`              | Requested calendar date                                                                    | Day to which the recovery observation belongs.                    |
| `sleep_duration_hours`  | `get_sleep_data` → `dailySleepDTO.sleepTimeSeconds` ÷ 3600                                 | Actual sleep duration, rounded to one decimal; not time in bed.   |
| `sleep_score`           | `get_sleep_data` → `dailySleepDTO.sleepScores.overall.value`                               | Garmin's composite sleep score.                                   |
| `sleep_consistency_pct` | `get_sleep_data` → `dailySleepDTO.sleepScores.sleepConsistency`                            | Best-effort sleep-consistency percentage; some firmware omits it. |
| `hrv_ms`                | `get_hrv_data` → `hrvSummary.lastNightAvg`                                                 | Last-night average heart-rate variability in milliseconds.        |
| `resting_hr_bpm`        | `get_rhr_day` resting-heart-rate metric; falls back to `get_user_summary.restingHeartRate` | Garmin's reported resting heart rate in beats per minute.         |
| `body_battery`          | Maximum measured level in the day's `get_body_battery` values                              | Peak daily charge on Garmin's 0–100 scale.                        |
| `stress_score`          | `get_user_summary.averageStressLevel`                                                      | Garmin's reported average stress score.                           |

Training readiness, respiration, body-battery charged/drained/minimum values, HRV baseline/status,
and body-battery events are intentionally excluded because the recovery log has no corresponding
fields.

## JSON output

For local automation, request the same rows as a JSON array:

```bash
uv run --package fitness-coach-garmin-sidecar garmin-sidecar wellness export \
  2026-08-09 2026-08-15 --format json
```

JSON is written to standard output. If an individual Garmin metric request fails, the command still
prints every date, omits the unavailable field, reports the affected date and metric to standard
error, and exits with status 1. Authentication, connection, and rate-limit failures during setup
exit with status 2. In particular, a cached-token connection or rate-limit failure is not followed
by a credential prompt.

## Privacy

Wellness exports contain sensitive health information. Keep terminal scrollback, shell redirection,
and copied chat content private. The sidecar does not save exports and never writes them into the
Garmin token directory, but commands such as `> wellness.json`, terminal logging, clipboard history,
or third-party shell tools can create additional copies.

Do not commit exports, Garmin tokens, passwords, or downloaded activity files to Git. Tokens remain
in the private token store described in [the sidecar guide](garmin-sidecar.md); passwords are entered
through a hidden prompt and are not persisted by this project.
