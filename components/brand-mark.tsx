import type { JSX } from "react";

export function BrandMark(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      className="brand-mark"
      viewBox="0 0 192 128"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M10 110 72 48c10-10 18-15 24-15 7 0 15 5 24 15l42 42 20 20"
        fill="none"
        stroke="currentColor"
        strokeLinecap="square"
        strokeLinejoin="round"
        strokeWidth="11"
      />
      <path
        d="m82 48 18-18c7-7 12-10 17-10 5 0 11 3 18 10l47 48"
        fill="none"
        stroke="currentColor"
        strokeLinecap="square"
        strokeLinejoin="round"
        strokeWidth="11"
      />
      <path
        d="m97 33 8-7 8 7"
        fill="none"
        stroke="var(--action)"
        strokeLinecap="square"
        strokeLinejoin="round"
        strokeWidth="8"
      />
    </svg>
  );
}
