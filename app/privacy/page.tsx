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
        <p className={styles.updated}>Last updated July 8, 2026</p>

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
            connection status, and an encrypted access token.
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
            backup purposes. You can disconnect Intervals.icu from your profile.
            To request account or data deletion, contact us at{" "}
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
