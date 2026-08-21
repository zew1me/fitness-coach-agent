import type { JSX, ReactNode } from "react";

import styles from "./status-card.module.css";

type StatusCardProps = Readonly<{
  body: string;
  children?: ReactNode;
  headingLevel?: "h1" | "h2";
  role?: "alert" | "status";
  title: string;
}>;

export function StatusCard({
  body,
  children,
  headingLevel = "h2",
  role,
  title,
}: StatusCardProps): JSX.Element {
  const Heading = headingLevel;

  return (
    <section
      aria-label={role === undefined ? undefined : title}
      className={styles.card}
      role={role}
    >
      <div className={styles.copy}>
        <Heading className={styles.title}>{title}</Heading>
        <p className={styles.body}>{body}</p>
      </div>
      {children === undefined ? null : (
        <div className={styles.content}>{children}</div>
      )}
    </section>
  );
}
