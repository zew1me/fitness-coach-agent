import { expect, test } from "@playwright/test";

import { mockAuthenticatedSession } from "./helpers/session";

const MOBILE_VIEWPORT = { width: 390, height: 844 };
const DESKTOP_VIEWPORT = { width: 1280, height: 800 };

test.describe("composer Enter key behavior (#157)", () => {
  test("mobile: Enter inserts a newline instead of submitting", async ({
    page,
  }) => {
    await page.setViewportSize(MOBILE_VIEWPORT);
    await mockAuthenticatedSession(page);

    let chatPostCount = 0;
    await page.route("**/api/chat", (route) => {
      chatPostCount += 1;
      return route.fulfill({
        status: 200,
        contentType: "text/plain",
        body: "",
      });
    });

    await page.goto("/");

    const textarea = page.getByTestId("composer-row").locator("textarea");
    await expect(textarea).toBeVisible();
    await textarea.click();
    await textarea.type("first line");
    await page.keyboard.press("Enter");
    await textarea.type("second line");

    await expect(textarea).toHaveValue("first line\nsecond line");
    expect(chatPostCount).toBe(0);

    await expect(
      page.getByText(/Tap the send button when you're ready/i),
    ).toBeVisible();
    await expect(page.getByText(/Shift\+Enter/i)).toHaveCount(0);
  });

  test("desktop: Enter submits and hint mentions Shift+Enter", async ({
    page,
  }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT);
    await mockAuthenticatedSession(page);

    await page.goto("/");

    const textarea = page.getByTestId("composer-row").locator("textarea");
    await expect(textarea).toBeVisible();
    await expect(
      page.getByText(/Use Shift\+Enter for a new line/i),
    ).toBeVisible();

    await textarea.click();
    await textarea.type("line one");
    await page.keyboard.down("Shift");
    await page.keyboard.press("Enter");
    await page.keyboard.up("Shift");
    await textarea.type("line two");
    await expect(textarea).toHaveValue("line one\nline two");
  });
});
