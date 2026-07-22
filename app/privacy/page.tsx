import type { Metadata } from "next";
import Link from "next/link";
import type { JSX } from "react";

import { siteConfig } from "../../lib/site";

import styles from "./privacy.module.css";

export const metadata: Metadata = {
  title: `Privacy Policy | ${siteConfig.appName}`,
  description: `${siteConfig.appName} privacy policy.`,
};

export default function PrivacyPage(): JSX.Element {
  return (
    <main className={styles.page}>
      <article className={styles.shell}>
        <Link className={styles.backLink} href="/">
          Back to Coach Arden
        </Link>
        <h1>Privacy Policy</h1>
        <p className={styles.updated}>Last updated July 22, 2026</p>

        <section>
          <h2>Overview</h2>
          <p>
            Coach Arden is an AI coaching app for endurance athletes. We use
            your information to operate your account, connect training data,
            generate coaching guidance, improve reliability, and protect the
            service. We do not sell your personal information or share it for
            cross-context behavioral advertising.
          </p>
        </section>

        <section>
          <h2>Information We Collect</h2>
          <p>
            We collect account and authentication details, athlete profile
            information, app usage data, chat messages, uploaded files, support
            messages, and technical logs. If you connect Intervals.icu, we store
            your Intervals athlete identifier, athlete name, granted scopes,
            connection status, and an encrypted access token. If you connect
            Strava, we store the Strava details described in the Strava Data
            section below.
          </p>
        </section>

        <section>
          <h2>Training and Intervals Data</h2>
          <p>
            With your permission, Coach Arden may read training, activity,
            wellness, and calendar data from Intervals.icu according to the
            scopes you approve. We use that data to plan, adapt, and review
            training for your account.
          </p>
        </section>

        <section>
          <h2>Strava Data</h2>
          <p>
            If you connect Strava, we store your Strava athlete identifier,
            athlete name, granted scopes, connection status, and encrypted
            access and refresh tokens. Tokens are encrypted at rest and are
            never returned to your browser. Through the Strava API (OAuth) we
            import activity <em>summaries only</em>: sport type, start date,
            moving and elapsed time, distance, elevation gain, average and
            maximum heart rate, average and weighted-average power, and average
            cadence. We do not collect GPS coordinates, maps or polylines,
            routes, segments, photos, or social data, and we never request write
            access.
          </p>
          <p>
            We use our hosting and storage subprocessors to import and store
            these summaries for non-AI account features such as your training
            calendar and Strava connection controls. Imported Strava data is
            excluded from OpenAI and other AI processing: it is not exposed to
            agent tools, specialist or delegation context, durable model state,
            or AI-facing derived results such as recent-activity tool results,
            compliance, training load, or threshold recalibration. Strava data
            is never sold or displayed to other users. Disconnecting Strava from
            your profile requests revocation of our access at Strava and, once
            revocation succeeds, deletes the Strava activities we imported; we
            confirm the deletion count on screen. To request export or deletion
            of your Strava data, contact us at{" "}
            <a href="mailto:privacy@coach.nigels.dev">
              privacy@coach.nigels.dev
            </a>
            .
          </p>
        </section>

        <section>
          <h2>How We Use and Share Information</h2>
          <p>
            Your data is accessible to you and to authorized Coach Arden
            developers when needed to operate, secure, debug, and support the
            app. We use service providers for hosting, storage, authentication,
            AI processing, analytics, and observability. These providers process
            information only as needed to provide their services to Coach Arden.
            We may also disclose information if required by law or to protect
            users, the app, or others from fraud, abuse, or security threats.
          </p>
        </section>

        <section>
          <h2>Retention and Deletion</h2>
          <p>
            We keep information while your account or connection is active and
            for as long as needed for service operation, security, legal, and
            backup purposes. You can disconnect Intervals.icu or Strava from
            your profile at any time; disconnecting Strava additionally revokes
            our Strava access and deletes the Strava activities we imported. To
            request account or data deletion, contact us at{" "}
            <a href="mailto:privacy@coach.nigels.dev">
              privacy@coach.nigels.dev
            </a>
            .
          </p>
        </section>

        <section>
          <h2>Contact</h2>
          <p>
            For privacy questions or requests, email{" "}
            <a href="mailto:privacy@coach.nigels.dev">
              privacy@coach.nigels.dev
            </a>
            .
          </p>
        </section>
      </article>
    </main>
  );
}
