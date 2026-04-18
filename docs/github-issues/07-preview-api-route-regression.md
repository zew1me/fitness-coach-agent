# Preview deployment serves the chat shell without the `/api/oauth/browser-token` backend route

## Summary

The latest deployed preview renders the new chat-first landing shell, but the first client bootstrap call to `POST /api/oauth/browser-token` returns `404`, leaving the app unable to establish a browser session.

## Observed behavior

- The deployed preview shows the new chat landing card.
- Browser devtools reports `404` for `/api/oauth/browser-token`.
- The broken route happens on initial page load before a user can meaningfully interact with the app.
- The UI never transitions into a working authenticated or logged-out state because the session bootstrap depends on this route.

## Reproduction

1. Open the latest Vercel preview deployment.
2. Wait for the homepage to load.
3. Inspect the network/console output.
4. Observe that `POST /api/oauth/browser-token` returns `404`.

## Expected behavior

- `POST /api/oauth/browser-token` should resolve to the FastAPI backend in `api/index.py`.
- If no browser session cookie is present, the endpoint should return the FastAPI `401` JSON payload rather than a missing-route `404`.
- The frontend should be able to continue into the logged-out prompt or the authenticated chat flow.

## Likely cause

The deployment is probably not routing preview traffic for `/api/*` into the FastAPI app defined in `api/index.py`.

Why this looks likely:

- The backend explicitly defines `@app.post("/api/oauth/browser-token")` in `api/index.py`.
- The frontend calls the same path from `lib/coach-api.ts`.
- A `404` on that exact path in the deployed preview suggests the route is missing from the deployed function map rather than failing inside FastAPI.
- This repo appears to rely on a single Python entrypoint file to handle multiple `/api/*` routes, but the current Vercel config may only be exposing `/api/index` or may not be packaging the Python function as expected in the hybrid Next.js deployment.

## Investigation checklist

- Verify which Python function routes are actually present in the preview deployment.
- Confirm whether `api/index.py` is being treated as a catch-all ASGI entrypoint or only as `/api/index`.
- Add or adjust the Vercel routing/build configuration so `/api/*` requests reach the FastAPI app consistently in preview and production.
- Add a deployment smoke test that fails if `/api/oauth/browser-token` is missing.
