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
        d="M16 106H176"
        fill="none"
        stroke="currentColor"
        strokeLinecap="square"
        strokeWidth="10"
      />
      <path
        d="M30 106 74 62c9-9 15-13 20-13 6 0 12 4 18 11l24 25"
        fill="none"
        stroke="currentColor"
        strokeLinecap="square"
        strokeLinejoin="round"
        strokeWidth="10"
      />
      <path
        d="m108 106 28-28c7-7 12-10 16-10 5 0 10 3 17 10l12 12"
        fill="none"
        stroke="currentColor"
        strokeLinecap="square"
        strokeLinejoin="round"
        strokeWidth="10"
      />
    </svg>
  );
}
