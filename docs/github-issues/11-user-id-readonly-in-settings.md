# User ID field in athlete settings must be read-only

## Summary

When the athlete settings/profile form is built (or if it already exists), the `user_id` field must be rendered as a non-editable display value — not an `<input>` the user can modify.

## Why

`user_id` is the Supabase auth UUID that ties an athlete's profile, check-ins, OAuth grants, and chat thread together. Allowing it to be edited would:

- Break all Supabase RLS policies scoped to the authenticated user's ID
- Allow an athlete to claim another user's data by submitting a different UUID
- Corrupt the `oauth_grants`, `oauth_authorization_codes`, and `oauth_refresh_tokens` rows that reference it
- Orphan the athlete's `ChatThread`

The ID is assigned by Supabase on sign-up and is immutable — there is no valid reason for it to be writable by the user.

## Expected behavior

- `user_id` is displayed as static text or a disabled/read-only field (e.g. `<input readOnly>` or a `<code>` block) so the athlete can copy it for support purposes.
- It is excluded from the form's submit payload — the backend derives `user_id` from the authenticated bearer token, not from the request body.
- The backend's `POST /api/profile` endpoint already enforces this via `enforce_user_access()` (`api/index.py`), but the frontend should make the constraint obvious.

## Implementation notes

- Render as `<input readOnly className="input" value={userId} />` or a plain `<code>` element, not an editable `<input>`.
- Do not include `user_id` in the controlled form state that gets submitted.
- The profile upsert payload (`AthleteProfile`) does include `user_id`, but it should be sourced from the authenticated session, not the form.
