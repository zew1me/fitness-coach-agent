/**
 * Touch-device chat interactions — guards issue #553.
 *
 * The other mobile specs only shrink a desktop viewport, so they never exercise
 * what makes a phone different: a coarse touch pointer, the `<meta viewport>`
 * contract, focus zoom on small inputs, and touch scrolling. This file runs
 * only in the `mobile-touch` Playwright project (see playwright.config.ts),
 * which emulates an iPhone-class device (`isMobile`, `hasTouch`, 3x DPR, mobile
 * user agent) on Chromium.
 *
 * Chromium is not WebKit: these tests cover the touch/viewport contract the app
 * controls, not Safari-engine rendering quirks.
 */
import { expect, test, type Page } from "@playwright/test";

import { mockAuthenticatedSession, mockLongThread } from "./helpers/session";

const LONG_THREAD_COUNT = 40;

// iOS Safari zooms the page when an input with a computed font-size below 16px
// gains focus, and never zooms back out.
const IOS_NO_FOCUS_ZOOM_MIN_FONT_PX = 16;

function composerTextarea(page: Page) {
  return page.getByTestId("composer-row").locator("textarea");
}

/** Drives a one-finger downward swipe through the Chromium touch pipeline. */
async function touchSwipeDown(
  page: Page,
  { x, fromY, distance }: { x: number; fromY: number; distance: number },
): Promise<void> {
  const steps = 10;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [{ x, y: fromY }],
  });
  for (let i = 1; i <= steps; i += 1) {
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ x, y: fromY + (distance * i) / steps }],
    });
  }
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await cdp.detach();
}

test.describe("chat on a touch device (#553)", () => {
  test.beforeEach(async ({ page }) => {
    await mockAuthenticatedSession(page);
  });

  test("the emulated device really is a coarse-pointer touch phone", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(composerTextarea(page)).toBeVisible();

    const env = await page.evaluate(() => ({
      coarse: window.matchMedia("(pointer: coarse)").matches,
      noHover: window.matchMedia("(hover: none)").matches,
      touchPoints: navigator.maxTouchPoints,
    }));
    expect(env.coarse).toBe(true);
    expect(env.noHover).toBe(true);
    expect(env.touchPoints).toBeGreaterThan(0);
  });

  test("the layout viewport matches the device width (meta viewport honored)", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(composerTextarea(page)).toBeVisible();

    // Without `width=device-width` a mobile browser lays out at ~980px and
    // shrinks the page, which also flips useIsMobile() to its desktop branch.
    const { innerWidth, scrollWidth, screenWidth } = await page.evaluate(
      () => ({
        innerWidth: window.innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
        screenWidth: window.screen.width,
      }),
    );
    expect(innerWidth).toBe(screenWidth);
    // No horizontal page scroll on a phone.
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
  });

  test("the composer is large enough that iOS does not zoom on focus", async ({
    page,
  }) => {
    await page.goto("/");
    const textarea = composerTextarea(page);
    await expect(textarea).toBeVisible();

    const fontPx = await textarea.evaluate((el) =>
      Number.parseFloat(getComputedStyle(el).fontSize),
    );
    expect(fontPx).toBeGreaterThanOrEqual(IOS_NO_FOCUS_ZOOM_MIN_FONT_PX);
  });

  test("tapping Send submits; the soft-keyboard Enter key inserts a newline", async ({
    page,
  }) => {
    const chatBodies: unknown[] = [];
    await page.route("**/api/chat", (route) => {
      chatBodies.push(route.request().postDataJSON());
      return route.fulfill({
        status: 200,
        contentType: "text/plain",
        body: "",
      });
    });

    await page.goto("/");
    const textarea = composerTextarea(page);
    await expect(textarea).toBeVisible();
    await expect(
      page.getByText(/Tap the send button when you're ready/i),
    ).toBeVisible();

    await textarea.tap();
    await expect(textarea).toBeFocused();
    await page.keyboard.type("first line");
    await page.keyboard.press("Enter");
    await page.keyboard.type("second line");
    await expect(textarea).toHaveValue("first line\nsecond line");
    expect(chatBodies).toHaveLength(0);

    await page.getByRole("button", { name: /^Send$/i }).tap();
    await expect.poll(() => chatBodies.length).toBe(1);
    expect(JSON.stringify(chatBodies[0])).toContain("first line\\nsecond line");
  });

  test("tapping the plus button opens the file picker", async ({ page }) => {
    await page.goto("/");
    const label = page.getByLabel("Add photo or activity file");
    await expect(label).toBeVisible();

    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      label.tap(),
    ]);
    expect(fileChooser.isMultiple()).toBe(true);
  });

  test("a touch swipe scrolls the transcript inside its pane, not the page", async ({
    page,
  }) => {
    await mockLongThread(page, LONG_THREAD_COUNT);
    // Count finished scrolls on any element (scrollend does not bubble, so
    // listen in the capture phase).
    await page.addInitScript(() => {
      const w = window as Window & { __scrollEnds?: number };
      w.__scrollEnds = 0;
      document.addEventListener(
        "scrollend",
        () => {
          w.__scrollEnds = (w.__scrollEnds ?? 0) + 1;
        },
        { capture: true },
      );
    });
    await page.goto("/");

    const pane = page.locator('[class*="messagesPane"]');
    // Loading the thread smooth-scrolls the pane to the newest message
    // (lib/use-message-window.ts). Wait for that scroll to finish (newest
    // message on screen and a scrollend fired) before reading the baseline.
    await expect(
      page.getByText(`Message ${LONG_THREAD_COUNT - 1}: keep it steady.`),
    ).toBeInViewport();
    await expect
      .poll(() =>
        page.evaluate(
          () => (window as Window & { __scrollEnds?: number }).__scrollEnds,
        ),
      )
      .toBeGreaterThan(0);
    const before = await pane.evaluate((el) => el.scrollTop);
    expect(before).toBeGreaterThan(0);

    // A real finger swipe (raw touchStart/touchMove/touchEnd), not a wheel or a
    // programmatic scrollTo. Dragging downward scrolls back toward older
    // messages, the opposite direction to the auto-scroll, so only the swipe
    // can lower scrollTop.
    const box = await pane.boundingBox();
    expect(box).not.toBeNull();
    await touchSwipeDown(page, {
      x: Math.round(box!.x + box!.width / 2),
      fromY: Math.round(box!.y + box!.height * 0.25),
      distance: 200,
    });

    await expect
      .poll(() => pane.evaluate((el) => el.scrollTop))
      .toBeLessThan(before);
    const pageScrollY = await page.evaluate(() => window.scrollY);
    expect(pageScrollY).toBe(0);
  });
});
