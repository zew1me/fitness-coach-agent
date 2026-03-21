import type { JSX } from "react";

type StatusCardProps = Readonly<{
  body: string;
  title: string;
}>;

export function StatusCard({ body, title }: StatusCardProps): JSX.Element {
  return (
    <section
      style={{
        border: "1px solid #d0d7de",
        borderRadius: "12px",
        marginTop: "1rem",
        padding: "1rem"
      }}
    >
      <h2>{title}</h2>
      <p>{body}</p>
    </section>
  );
}
