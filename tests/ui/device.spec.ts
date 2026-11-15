/**
 * Device authorization page (RFC 8628) — static states only; the backend
 * endpoints are not needed because every state is driven by query params.
 */
import { test, expect } from "@playwright/test";

test.describe("/device", () => {
  test("shows the code entry form", async ({ page }) => {
    await page.goto("/device");
    await expect(
      page.getByRole("heading", { name: "Connect the coach CLI" }),
    ).toBeVisible();
    await expect(page.getByLabel("Code")).toBeVisible();
  });

  test("shows the confirm step", async ({ page }) => {
    await page.goto(
      "/device?step=confirm&user_code=BCDF-GHJK&client_id=fitness-coach-cli&scope=profile:read",
    );
    await expect(page.getByText("BCDF-GHJK")).toBeVisible();
    await expect(
      page.getByText("Client: Coach CLI (fitness-coach-cli)"),
    ).toBeVisible();
    await expect(page.getByText("profile:read")).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Deny" })).toBeVisible();
  });

  test("shows the approved state", async ({ page }) => {
    await page.goto("/device?status=approved");
    await expect(
      page.getByText(
        "CLI connected. You can close this tab and return to your terminal.",
      ),
    ).toBeVisible();
  });
});
