## Summary

Move the frontend from scaffold pages to a usable coaching workflow.

## Current state

- `app/page.tsx` is still a static overview.
- Login, consent, and profile pages exist but remain scaffold-level.
- There is no end-to-end browser flow for profile editing, check-ins, uploads, and generated plans.

## Scope

- Build an authenticated profile editor.
- Add check-in submission UI.
- Add upload flow for screenshots/files.
- Show generated plan output and rationale.
