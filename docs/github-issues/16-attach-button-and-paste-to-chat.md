# Fix attachment button hit target; add cmd+V paste-image to chat

## Summary

Two related attachment UX bugs:

1. **+ button is not clickable.** The attachment button (`+`) either has a z-index issue
   or its click event is intercepted before it reaches the `<input type="file">` trigger.
   Clicking the `+` does nothing.

2. **No paste-image support.** On macOS (and other platforms), pressing `Cmd+V` (or
   `Ctrl+V`) with an image in the clipboard should paste the image directly into the
   chat composer — the same flow as choosing a file, including an inline thumbnail
   preview. Currently the paste event is not handled.

## Desired behavior

### + button
- Clicking the `+` button opens the OS file picker, filtered to `image/*`.
- The button is reachable via keyboard (Tab + Enter/Space) as well as mouse.

### Paste image
- Pressing `Cmd+V` / `Ctrl+V` while the composer textarea (or the composer card) is
  focused, and the clipboard contains an image (e.g. a screenshot), attaches the image
  directly to the outgoing message.
- A thumbnail preview renders immediately in the upload tray (same as the drag-and-drop
  tray UI).
- The image is uploaded via the same presign flow as file-picker attachments.
- If the clipboard contains text, the normal paste behavior (insert text into textarea)
  is preserved.

## Likely cause (+ button)

The `<input type="file">` is `position: absolute` or hidden in a way that its stacking
context sits below the button, or the click handler uses `.current?.click()` but
`current` is null at call time.

## Implementation notes

- Listen for `paste` events on the composer `<textarea>` (or the wrapping `<div>`) and
  check `event.clipboardData.items` for `image/*` entries.
- Convert the `DataTransferItem` to a `File` and push it through the existing
  `handleFileSelect` path.
- A `pasteImage` unit test can simulate a paste event with a mock clipboard item.

## Acceptance criteria

- [ ] Clicking `+` opens the file picker
- [ ] `Cmd+V` / `Ctrl+V` with an image in the clipboard attaches it to the message
- [ ] Text paste still works normally in the textarea
- [ ] Attached-image thumbnail renders in the upload tray before send
- [ ] Image is uploaded via the presign flow and included in the outgoing message
- [ ] `bun run check` passes
