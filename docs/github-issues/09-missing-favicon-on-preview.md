# Preview deployment is missing `/favicon.ico`

## Summary

The deployed preview returns `404` for `/favicon.ico`, which creates avoidable console noise and makes the app feel unfinished.

## Observed behavior

- Browser devtools reports `Failed to load resource: the server responded with a status of 404` for `/favicon.ico`.

## Expected behavior

- The preview and production site should ship a valid favicon reference with no missing asset errors.

## Likely cause

There is no favicon asset or explicit metadata icon configuration in the current app shell, so browsers fall back to requesting `/favicon.ico` and receive a `404`.

## Fix ideas

- Add a real favicon asset under `app/` or `public/`.
- Wire the icon through Next.js metadata so previews and production use the same asset.
