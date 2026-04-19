# Fix low-contrast text in assistant chat bubbles

## Summary

The assistant message bubbles render white-on-near-white: the background is
`rgba(255, 255, 255, 0.94)` but the text color is not explicitly set, so it inherits a
muted or light value from the page and fails WCAG AA contrast requirements. The text is
hard to read, especially on the gradient background.

## Observed behavior

- Assistant bubbles: near-white background, light/muted text — poor contrast.
- User bubbles: blue gradient with white text — acceptable contrast.
- The `messageText` class has no explicit color, inheriting whatever the theme provides.
- Timestamps rendered in `.attachmentName` are also hard to read.

## Expected behavior

- Assistant bubble text: minimum 4.5:1 contrast ratio on the bubble background (WCAG AA).
- Explicit `color` set on `.messageText` so the value does not depend on inheritance.
- Timestamp text: sufficiently muted but still legible (at least 3:1 against the bubble).

## Files to change

- `components/coach-chat.module.css` — set explicit colors on `.assistantBubble`,
  `.messageText`, and message metadata spans.
- Possibly `app/globals.css` if base body text color is the root cause.

## Acceptance criteria

- [ ] Assistant bubble text is clearly legible at normal viewing distances
- [ ] Contrast ratio for `.messageText` on `.assistantBubble` background ≥ 4.5:1
- [ ] Timestamp/metadata text ≥ 3:1 contrast on its background
- [ ] User bubble text unchanged (already passes)
- [ ] `bun run check` passes
