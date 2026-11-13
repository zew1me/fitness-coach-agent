// Keeps Tab and Shift+Tab inside an open modal drawer (role="dialog") by
// wrapping focus between its first and last focusable elements. Shared by the
// profile drawer and the calendar day-detail panel.
const DRAWER_FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[href]",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function trapDrawerFocus(
  event: KeyboardEvent,
  drawer: HTMLElement | null,
): void {
  const focusableElements = Array.from(
    drawer?.querySelectorAll<HTMLElement>(DRAWER_FOCUSABLE_SELECTOR) ?? [],
  );
  const firstElement = focusableElements[0];
  const lastElement = focusableElements.at(-1);
  if (firstElement === undefined || lastElement === undefined) return;

  if (event.shiftKey && document.activeElement === firstElement) {
    event.preventDefault();
    lastElement.focus();
  } else if (!event.shiftKey && document.activeElement === lastElement) {
    event.preventDefault();
    firstElement.focus();
  }
}
