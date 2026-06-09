import { expect, test } from "@playwright/test";

test.describe("invite-gated login flow", () => {
  test("returning users can request a code without an invite code", async ({ page }) => {
    let requestBody: unknown = null;
    await page.route("/api/auth/request-otp", async (route) => {
      requestBody = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "otp_sent", inviteRequired: false }),
      });
    });

    await page.goto("/login");
    await page.getByLabel("Email").fill("athlete@example.com");
    await page.getByRole("button", { name: /send code/i }).click();

    await expect(page.getByLabel(/6-digit code/i)).toBeVisible();
    expect(requestBody).toEqual({
      email: "athlete@example.com",
      inviteCode: null,
      returnTo: "/consent",
    });
  });

  test("first-time users enter an invite code before the OTP form appears", async ({ page }) => {
    const requestBodies: unknown[] = [];
    await page.route("/api/auth/request-otp", async (route) => {
      requestBodies.push(route.request().postDataJSON());
      const isFirstAttempt = requestBodies.length === 1;
      await route.fulfill({
        status: isFirstAttempt ? 409 : 200,
        contentType: "application/json",
        body: JSON.stringify(
          isFirstAttempt
            ? {
                error: "invite_required",
                message: "This coach is currently accepting referred athletes only. Enter your invite code to get started.",
              }
            : { status: "otp_sent", inviteRequired: false }
        ),
      });
    });

    await page.goto("/login");
    await page.getByLabel("Email").fill("new@example.com");
    await page.getByRole("button", { name: /send code/i }).click();

    await expect(page.getByLabel("Invite code")).toBeVisible();
    await expect(page.getByText("This coach is currently accepting referred athletes only. Enter your invite code to get started.")).toBeVisible();

    await page.getByLabel("Invite code").fill("alpha-access");
    await page.getByRole("button", { name: /send code/i }).click();

    await expect(page.getByLabel(/6-digit code/i)).toBeVisible();
    expect(requestBodies).toEqual([
      {
        email: "new@example.com",
        inviteCode: null,
        returnTo: "/consent",
      },
      {
        email: "new@example.com",
        inviteCode: "alpha-access",
        returnTo: "/consent",
      },
    ]);
  });
});
