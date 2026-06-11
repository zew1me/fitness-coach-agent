import { expect, test, type Locator } from "@playwright/test";

import { mockAuthenticatedSession } from "./helpers/session";

async function boundingBox(
  locator: Locator,
): Promise<NonNullable<Awaited<ReturnType<Locator["boundingBox"]>>>> {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  return box!;
}

function rectanglesOverlap(
  first: { height: number; width: number; x: number; y: number },
  second: { height: number; width: number; x: number; y: number },
): boolean {
  return (
    first.x < second.x + second.width &&
    first.x + first.width > second.x &&
    first.y < second.y + second.height &&
    first.y + first.height > second.y
  );
}

test.describe("mobile chat composer layout (#154)", () => {
  test("keeps the textarea and icon send button inline without overlap", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockAuthenticatedSession(page);

    await page.goto("/");
    const composerRow = page.getByTestId("composer-row");
    const attachButton = composerRow.locator(
      '[aria-label="Add photo or activity file"]',
    );
    const textarea = composerRow.locator("textarea");
    const sendButton = composerRow.getByRole("button", { name: /^Send$/i });

    await expect(composerRow).toBeVisible();
    await expect(attachButton).toBeVisible();
    await expect(textarea).toBeVisible();
    await expect(sendButton).toBeVisible();

    const rowBox = await boundingBox(composerRow);
    const attachBox = await boundingBox(attachButton);
    const textareaBox = await boundingBox(textarea);
    const sendBox = await boundingBox(sendButton);

    expect(textareaBox.x).toBeGreaterThanOrEqual(attachBox.x + attachBox.width);
    expect(sendBox.x).toBeGreaterThanOrEqual(textareaBox.x + textareaBox.width);
    expect(rectanglesOverlap(textareaBox, sendBox)).toBe(false);
    expect(sendBox.x + sendBox.width).toBeLessThanOrEqual(
      rowBox.x + rowBox.width,
    );
  });
});
