# Add light / dark / system theme picker to Settings drawer

## Summary

The app currently has no theme control. Users on dark-mode systems get the light theme
forced on them, and there is no way to override. The Settings drawer should include a
radio-button group that lets the athlete choose **Light**, **Dark**, or **System** (follow
the OS preference). The selection should persist in `localStorage` and apply immediately
without a page reload.

## Desired behavior

- Three options surfaced as a radio group inside the existing Settings drawer:
  `Light` | `Dark` | `System`
- Default on first visit: **System** (respects `prefers-color-scheme`).
- Selecting Light or Dark overrides the OS setting.
- Preference persists across page reloads via `localStorage`.
- Applies via a `data-theme="light"|"dark"` attribute on `<html>` (or equivalent CSS
  custom-property approach).
- No flash of wrong theme on hard reload (blocking `<script>` snippet in `<head>`).

## Implementation notes

- CSS variables in `globals.css` (or a new `theme.css`) define all palette tokens for
  both themes.
- A `ThemeProvider` component or a small hook (`useTheme`) reads localStorage + system
  preference and writes the data attribute.
- The blocking snippet in `<head>` prevents FOUC — it reads localStorage synchronously
  before React hydrates.
- The radio group in the Settings drawer calls the `setTheme` helper.

## Acceptance criteria

- [ ] Settings drawer shows Light / Dark / System radio buttons
- [ ] Selecting a value applies the theme immediately
- [ ] Selection persists across hard reloads
- [ ] System mode follows `prefers-color-scheme` and reacts to OS changes
- [ ] No flash of wrong theme on page load
- [ ] `bun run check` passes
