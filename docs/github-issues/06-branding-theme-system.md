# Establish product branding, paired theme system, and sport-agnostic icon direction

## Summary

Create a disciplined brand system for the product and adapt the app UI to reflect it. The core direction is an off-white default theme for long chat-heavy sessions, a true paired deep-navy dark theme for early-morning and late-night use, and a simple sport-agnostic mountain symbol. The chosen default direction is the `Horizon` mark: a calm mountain-and-horizon silhouette with no letterforms and no orange accent in the final icon.

## Goals

- Introduce a single design system expressed in two modes, not two unrelated visual treatments.
- Make the light theme the daytime default and the dark theme its direct counterpart.
- Add a `Light / Dark / System` selector so users can explicitly control theme behavior.
- Keep dark mode stylistically aligned with light mode rather than simply inverted.
- Establish an icon direction that is symbolic, minimal, and not tied to one sport.

## Theme Direction

### Light Theme

- Background: `#F8FAFC`
- Surface: `#FFFFFF`
- Primary text: `#0F172A`
- Accent: `#2A9D8F`
- Action / effort color: `#F97316`

### Dark Theme

- Background: `#0B1220`
- Surface: `#0F172A`
- Primary text: `#E2E8F0`
- Accent: `#2A9D8F`
- Action / effort color: `#F97316`

## Rules

- Treat dark mode as the same product at night, not a different app.
- Avoid pure black and pure white; keep contrast controlled for long sessions.
- Preserve hierarchy between page background, cards, and elevated surfaces in both modes.
- Keep teal and orange consistent across themes; do not invent a second accent palette for dark mode.
- Use orange for actions, state changes, and intensity moments only. Do not turn it into the default chrome color.

## Chat / product UI notes

- Light coach bubble should use a very light teal tint.
- Dark coach bubble should use a deeper muted teal tint.
- User surfaces should stay quiet and neutral in both themes.
- Theme switching should feel polished, with a quick crossfade rather than a hard flash.

## Theme selector

- Add three explicit modes: `Light`, `Dark`, and `System`.
- Keep the selector compact and consistent with the icon language.
- Support system preference while still allowing the user to override it.

## Brand mark direction

- Use mountain / ridge imagery inspired by the simple line sketch direction.
- Keep it symbolic rather than literal or illustrative.
- Avoid body-shape, physique, or single-sport imagery.
- Keep the final selected mark neutral and monochrome; do not rely on an orange summit accent in the icon itself.
- Do not use letters as the main identity.

## Deliverables

- Shared theme tokens and paired light/dark mode support in the app shell.
- Theme selector wired into the UI with persisted preference and system fallback.
- Updated homepage/dashboard styling that reflects the new brand direction.
- Multiple simple mountain mark options for review and iteration, with `Horizon` selected as the default.
