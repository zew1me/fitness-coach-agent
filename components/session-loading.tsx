import type { JSX } from "react";

import { siteConfig } from "../lib/site";

import { BrandMark } from "./brand-mark";
import styles from "./session-loading.module.css";

export function SessionLoading(): JSX.Element {
  return (
    <main aria-busy="true" className={styles.page}>
      <section aria-labelledby="session-loading-title" className={styles.card}>
        <div className={styles.brand}>
          <span className={styles.mark}>
            <BrandMark />
          </span>
          <span className={styles.brandName}>{siteConfig.appName}</span>
        </div>

        <div className={styles.copy}>
          <p className={styles.eyebrow}>Endurance coaching, made personal</p>
          <h1 className={styles.title} id="session-loading-title">
            Your coach is warming up.
          </h1>
          <p className={styles.text}>
            We&apos;re checking your sign-in and getting your coaching space
            ready. This usually takes just a moment.
          </p>
        </div>

        <div aria-hidden="true" className={styles.course}>
          <span className={styles.courseLine} />
          <span className={styles.finishLine} />
          <span className={styles.athlete}>🏃</span>
          <span className={styles.athlete}>🚴</span>
          <span className={styles.athlete}>🏊</span>
        </div>

        <p aria-atomic="true" className={styles.status} role="status">
          <span aria-hidden="true" className={styles.statusPulse} />
          No refresh needed. We&apos;ll take you to the right place.
        </p>
      </section>
    </main>
  );
}
