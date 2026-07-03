"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { JSX, RefObject } from "react";

import {
  buildCalendarWeeks,
  type CalendarDayItems,
  calendarDateRange,
  groupCalendarItemsByDay,
  monthLabel,
} from "../lib/calendar-view";
import { loadCalendar } from "../lib/coach-api";
import type {
  CalendarActivity,
  CalendarPlannedWorkout,
  CalendarResponse,
} from "../lib/schemas";
import { siteConfig } from "../lib/site";
import type { BrowserTokenResponse } from "../lib/types";
import { useBrowserSession } from "../lib/use-browser-session";

import styles from "./coach-calendar.module.css";

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MAX_CHIPS_PER_DAY = 3;
const EMPTY_DAY: CalendarDayItems = { planned: [], activities: [] };

function localTodayIso(): string {
  // en-CA formats as YYYY-MM-DD in the viewer's local timezone, so "today"
  // matches the athlete's wall clock rather than UTC.
  return new Intl.DateTimeFormat("en-CA").format(new Date());
}

function longDayLabel(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${iso}T00:00:00Z`));
}

function formatMinutes(totalMinutes: number): string {
  const hours = Math.floor(totalMinutes / 60);
  const minutes = Math.round(totalMinutes % 60);
  if (hours === 0) return `${minutes}m`;
  return minutes === 0 ? `${hours}h` : `${hours}h ${minutes}m`;
}

function formatDistance(meters: number): string {
  return `${(meters / 1000).toFixed(1)} km`;
}

function workoutTypeLabel(workoutType: string): string {
  return workoutType.replaceAll("_", " ");
}

function definedNumber(value: number | null | undefined): value is number {
  return value !== null && value !== undefined;
}

function plannedWorkoutPills(workout: CalendarPlannedWorkout): string[] {
  const pills = [
    workout.sport,
    workoutTypeLabel(workout.workout_type),
    workout.status,
  ];
  if (definedNumber(workout.target_duration_minutes)) {
    pills.push(formatMinutes(workout.target_duration_minutes));
  }
  if (definedNumber(workout.target_tss)) {
    pills.push(`${Math.round(workout.target_tss)} TSS`);
  }
  if (definedNumber(workout.target_distance_meters)) {
    pills.push(formatDistance(workout.target_distance_meters));
  }
  return pills;
}

function activityPills(activity: CalendarActivity): string[] {
  const pills: string[] = [];
  if (definedNumber(activity.duration_seconds)) {
    pills.push(formatMinutes(activity.duration_seconds / 60));
  }
  if (definedNumber(activity.distance_meters)) {
    pills.push(formatDistance(activity.distance_meters));
  }
  if (definedNumber(activity.tss)) {
    pills.push(`${Math.round(activity.tss)} TSS`);
  }
  if (definedNumber(activity.avg_hr_bpm)) {
    pills.push(`${activity.avg_hr_bpm} bpm`);
  }
  if (definedNumber(activity.rpe)) {
    pills.push(`RPE ${activity.rpe}`);
  }
  return pills;
}

function plannedChipLabel(workout: CalendarPlannedWorkout): string {
  return `${workout.sport}: ${workout.title}`;
}

function activityChipLabel(activity: CalendarActivity): string {
  const duration =
    activity.duration_seconds !== null &&
    activity.duration_seconds !== undefined
      ? ` · ${formatMinutes(activity.duration_seconds / 60)}`
      : "";
  return `${activity.sport}${duration}`;
}

export function CoachCalendar(): JSX.Element {
  const session = useBrowserSession();
  if (session.loading) {
    return (
      <main className={styles.landingWrap}>
        <section className={styles.statusBanner}>
          <p className={styles.meta}>Checking your browser session…</p>
        </section>
      </main>
    );
  }
  if (session.token === null) {
    return <LoggedOutCalendar />;
  }
  return <SignedInCalendar token={session.token} />;
}

function LoggedOutCalendar(): JSX.Element {
  return (
    <main className={styles.landingWrap}>
      <section className={styles.landingCard}>
        <p className={styles.eyebrow}>Training calendar</p>
        <h1 className={styles.landingTitle}>
          See your plan and training history at a glance.
        </h1>
        <p className={styles.landingText}>
          Sign in to view your planned workouts and recorded sessions for the
          past six weeks and the eight weeks ahead.
        </p>
        <Link
          className={styles.primaryButton}
          href="/login?return_to=/calendar"
        >
          Continue with magic link
        </Link>
      </section>
    </main>
  );
}

function SignedInCalendar({
  token,
}: Readonly<{ token: BrowserTokenResponse }>): JSX.Element {
  const todayIso = useMemo(() => localTodayIso(), []);
  const range = useMemo(() => calendarDateRange(todayIso), [todayIso]);
  const calendarQuery = useQuery<CalendarResponse, Error>({
    queryKey: ["calendar", token.user_id, range.start, range.end],
    queryFn: ({ signal }) =>
      loadCalendar(range.start, range.end, fetch, signal),
  });
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const weeks = useMemo(
    () => buildCalendarWeeks(range.start, range.end),
    [range],
  );
  const byDay = useMemo(
    () =>
      calendarQuery.data === undefined
        ? new Map<string, CalendarDayItems>()
        : groupCalendarItemsByDay(calendarQuery.data),
    [calendarQuery.data],
  );

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <div className={styles.frame}>
          <header className={styles.topbar}>
            <div className={styles.brandBlock}>
              <p className={styles.brand}>{siteConfig.appName}</p>
              <span className={styles.meta}>Training calendar</span>
            </div>
            <div className={styles.topbarActions}>
              <Link
                className={styles.navButton}
                data-testid="calendar-open-chat"
                href="/"
              >
                Back to chat
              </Link>
            </div>
          </header>
          <div className={styles.gridHeader}>
            <div className={styles.legend}>
              <span className={styles.legendItem}>
                <span
                  className={`${styles.legendSwatch} ${styles.legendPlanned}`}
                />
                Planned workout
              </span>
              <span className={styles.legendItem}>
                <span
                  className={`${styles.legendSwatch} ${styles.legendActivity}`}
                />
                Recorded activity
              </span>
            </div>
            <div className={styles.weekdayRow}>
              {WEEKDAY_LABELS.map((label) => (
                <span className={styles.weekdayCell} key={label}>
                  {label}
                </span>
              ))}
            </div>
          </div>
          <CalendarBody
            byDay={byDay}
            error={calendarQuery.isError ? calendarQuery.error.message : null}
            loading={calendarQuery.isPending}
            onRetry={() => void calendarQuery.refetch()}
            onSelectDay={setSelectedDay}
            todayIso={todayIso}
            weeks={weeks}
          />
        </div>
      </div>
      {selectedDay !== null ? (
        <DayDetailPanel
          dayIso={selectedDay}
          items={byDay.get(selectedDay) ?? EMPTY_DAY}
          onClose={() => setSelectedDay(null)}
        />
      ) : null}
    </main>
  );
}

function CalendarBody({
  weeks,
  byDay,
  todayIso,
  loading,
  error,
  onRetry,
  onSelectDay,
}: Readonly<{
  weeks: string[][];
  byDay: Map<string, CalendarDayItems>;
  todayIso: string;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onSelectDay: (_dayIso: string) => void;
}>): JSX.Element {
  const todayCellRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (loading) return;
    const todayCell = todayCellRef.current;
    if (todayCell !== null && typeof todayCell.scrollIntoView === "function") {
      todayCell.scrollIntoView({ block: "center" });
    }
  }, [loading]);

  if (loading) {
    return (
      <div className={styles.statusBanner}>
        <p className={styles.meta}>Loading your training calendar…</p>
      </div>
    );
  }
  if (error !== null) {
    return (
      <div className={styles.statusBanner}>
        <p className={styles.meta}>{error}</p>
        <button className={styles.drawerClose} onClick={onRetry} type="button">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className={styles.calendarScroll} data-testid="calendar-grid">
      {weeks.map((week, weekIndex) => {
        const monday = week[0] ?? "";
        const previousMonday = weeks[weekIndex - 1]?.[0];
        const startsNewMonth =
          previousMonday === undefined ||
          monthLabel(previousMonday) !== monthLabel(monday);
        return (
          <div key={monday}>
            {startsNewMonth ? (
              <h2 className={styles.monthRow}>{monthLabel(monday)}</h2>
            ) : null}
            <div className={styles.weekRow}>
              {week.map((dayIso) => (
                <DayCell
                  dayIso={dayIso}
                  items={byDay.get(dayIso) ?? EMPTY_DAY}
                  key={dayIso}
                  onSelect={onSelectDay}
                  todayIso={todayIso}
                  todayRef={dayIso === todayIso ? todayCellRef : null}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DayCell({
  dayIso,
  items,
  todayIso,
  todayRef,
  onSelect,
}: Readonly<{
  dayIso: string;
  items: CalendarDayItems;
  todayIso: string;
  todayRef: RefObject<HTMLButtonElement | null> | null;
  onSelect: (_dayIso: string) => void;
}>): JSX.Element {
  const isToday = dayIso === todayIso;
  const isPast = dayIso < todayIso;
  const cellClass = [
    styles.dayCell,
    isToday ? styles.dayToday : "",
    isPast && !isToday ? styles.dayPast : "",
  ]
    .filter((name) => name.length > 0)
    .join(" ");

  const chips: JSX.Element[] = [
    ...items.planned.map((workout) => (
      <span
        className={`${styles.chip} ${styles.chipPlanned}`}
        data-status={workout.status}
        data-testid="calendar-chip-planned"
        key={`planned-${workout.id}`}
        title={plannedChipLabel(workout)}
      >
        <span className={styles.chipLabel}>{plannedChipLabel(workout)}</span>
      </span>
    )),
    ...items.activities.map((activity) => (
      <span
        className={`${styles.chip} ${styles.chipActivity}`}
        data-testid="calendar-chip-activity"
        key={`activity-${activity.id}`}
        title={activityChipLabel(activity)}
      >
        <span className={styles.chipLabel}>{activityChipLabel(activity)}</span>
      </span>
    )),
  ];
  const hiddenCount = chips.length - MAX_CHIPS_PER_DAY;

  return (
    <button
      aria-label={longDayLabel(dayIso)}
      className={cellClass}
      data-date={dayIso}
      data-testid="calendar-day"
      data-today={isToday ? "true" : undefined}
      onClick={() => onSelect(dayIso)}
      ref={todayRef}
      type="button"
    >
      <span className={styles.dayNumber}>
        {Number(dayIso.slice(8))}
        {isToday ? <span className={styles.todayLabel}>Today</span> : null}
      </span>
      <span className={styles.chipList}>
        {chips.slice(0, MAX_CHIPS_PER_DAY)}
        {hiddenCount > 0 ? (
          <span className={styles.moreCount}>+{hiddenCount} more</span>
        ) : null}
      </span>
    </button>
  );
}

function DayDetailPanel({
  dayIso,
  items,
  onClose,
}: Readonly<{
  dayIso: string;
  items: CalendarDayItems;
  onClose: () => void;
}>): JSX.Element {
  const isEmpty = items.planned.length === 0 && items.activities.length === 0;
  return (
    <div
      className={styles.drawerBackdrop}
      onClick={onClose}
      role="presentation"
    >
      <aside
        aria-label={`Details for ${longDayLabel(dayIso)}`}
        className={styles.drawer}
        data-testid="calendar-day-detail"
        onClick={(event) => event.stopPropagation()}
      >
        <div className={styles.drawerHeader}>
          <div>
            <h2 className={styles.drawerTitle}>{longDayLabel(dayIso)}</h2>
            <p className={styles.drawerText}>
              Planned workouts and recorded sessions for this day.
            </p>
          </div>
          <button
            className={styles.drawerClose}
            data-testid="calendar-detail-close"
            onClick={onClose}
            type="button"
          >
            Close
          </button>
        </div>

        {isEmpty ? (
          <p className={styles.emptyDetail}>
            Nothing planned or recorded for this day.
          </p>
        ) : null}

        {items.planned.length > 0 ? (
          <section className={styles.detailSection}>
            <h3 className={styles.detailHeading}>Planned</h3>
            {items.planned.map((workout) => (
              <PlannedWorkoutDetail key={workout.id} workout={workout} />
            ))}
          </section>
        ) : null}

        {items.activities.length > 0 ? (
          <section className={styles.detailSection}>
            <h3 className={styles.detailHeading}>Recorded</h3>
            {items.activities.map((activity) => (
              <ActivityDetail activity={activity} key={activity.id} />
            ))}
          </section>
        ) : null}
      </aside>
    </div>
  );
}

function PlannedWorkoutDetail({
  workout,
}: Readonly<{ workout: CalendarPlannedWorkout }>): JSX.Element {
  return (
    <article className={`${styles.detailItem} ${styles.detailItemPlanned}`}>
      <h4 className={styles.detailItemTitle}>{workout.title}</h4>
      <div className={styles.detailMetaRow}>
        {plannedWorkoutPills(workout).map((pill) => (
          <span className={styles.detailPill} key={pill}>
            {pill}
          </span>
        ))}
      </div>
      {typeof workout.description === "string" &&
      workout.description.length > 0 ? (
        <p className={styles.detailNotes}>{workout.description}</p>
      ) : null}
    </article>
  );
}

function ActivityDetail({
  activity,
}: Readonly<{ activity: CalendarActivity }>): JSX.Element {
  return (
    <article className={`${styles.detailItem} ${styles.detailItemActivity}`}>
      <h4 className={styles.detailItemTitle}>{activity.sport}</h4>
      <div className={styles.detailMetaRow}>
        {activityPills(activity).map((pill) => (
          <span className={styles.detailPill} key={pill}>
            {pill}
          </span>
        ))}
      </div>
      {typeof activity.athlete_notes === "string" &&
      activity.athlete_notes.length > 0 ? (
        <p className={styles.detailNotes}>{activity.athlete_notes}</p>
      ) : null}
    </article>
  );
}
