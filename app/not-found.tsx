import Link from "next/link";
import type { JSX } from "react";

import { StatusCard } from "../components/status-card";

import styles from "./not-found.module.css";

export default function NotFound(): JSX.Element {
  return (
    <main className={styles.page}>
      <div className={styles.content}>
        <StatusCard
          body="The page may have moved, or the address may be incorrect."
          headingLevel="h1"
          title="Page not found"
        >
          <Link className={styles.link} href="/">
            Return to coach
          </Link>
        </StatusCard>
      </div>
    </main>
  );
}
