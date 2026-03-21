## Summary

Harden the ChatGPT-facing OAuth flow so it behaves like a real authorization server instead of an in-memory scaffold.

## Current state

- `backend/services/auth.py` still issues JWT codes/tokens directly.
- `/api/oauth/authorize` still hardcodes `demo-user`.
- Revoke and refresh behavior are still placeholder-level.

## Scope

- Persist grants, auth codes, and refresh tokens.
- Tie the authorize flow to a real logged-in user session.
- Implement token refresh and revocation semantics.
- Validate scopes and client metadata more strictly.
