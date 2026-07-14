import type { JSX } from "react";

import { WORKOUT_FAMILY_LEGEND } from "../lib/calendar-workout-visuals";

import styles from "./coach-calendar.module.css";

const ENTRY_MARKERS = [
  { label: "Scheduled plan", marker: "○", status: "scheduled" },
  { label: "Completed plan", marker: "✓", status: "completed" },
  { label: "Recorded activity", marker: "●", status: "recorded" },
] as const;

export function CalendarLegend(): JSX.Element {
  return (
    <div className={styles.legends}>
      <div
        aria-label="Workout type colors"
        className={styles.legendGroup}
        role="group"
      >
        <span className={styles.legendTitle}>Type</span>
        <div className={styles.legend}>
          {WORKOUT_FAMILY_LEGEND.map(({ family, label }) => (
            <span className={styles.legendItem} key={family}>
              <span
                aria-hidden="true"
                className={`${styles.legendSwatch} ${styles.workoutVisual}`}
                data-workout-family={family}
              />
              {label}
            </span>
          ))}
        </div>
      </div>
      <div
        aria-label="Workout entry markers"
        className={styles.legendGroup}
        role="group"
      >
        <span className={styles.legendTitle}>Entry</span>
        <div className={styles.legend}>
          {ENTRY_MARKERS.map(({ label, marker, status }) => (
            <span className={styles.legendItem} key={status}>
              <span
                aria-hidden="true"
                className={styles.legendMarker}
                data-entry-status={status}
              >
                {marker}
              </span>
              {label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
