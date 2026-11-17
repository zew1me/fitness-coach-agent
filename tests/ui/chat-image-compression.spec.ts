import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

import { IMAGE_COMPRESSION_WORKER_URL } from "../../lib/image-compression";

import { mockAuthenticatedSession } from "./helpers/session";

for (const corruptWorker of [false, true]) {
  test(`optimizes a large PNG with ${corruptWorker ? "SRI failure and safe main-thread fallback" : "a verified CDN worker"}`, async ({
    page,
  }) => {
    test.setTimeout(60_000);
    await mockAuthenticatedSession(page);
    const cdnRequests: string[] = [];
    const workerRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).hostname === "cdn.jsdelivr.net")
        cdnRequests.push(request.url());
    });
    page.on("worker", (worker) => workerRequests.push(worker.url()));
    const workerScript = readFileSync(
      require.resolve("browser-image-compression"),
    );
    await page.route("https://cdn.jsdelivr.net/**", (route) =>
      route.fulfill({
        contentType: "text/javascript",
        headers: { "access-control-allow-origin": "*" },
        // A bad response must never execute; only the bundled dependency may run.
        body: corruptWorker
          ? Buffer.from("throw new Error('UNVERIFIED CODE RAN')")
          : workerScript,
      }),
    );
    let releaseUpload!: () => void;
    const uploadGate = new Promise<void>((resolve) => {
      releaseUpload = resolve;
    });
    let uploadedJpeg: Buffer | undefined;
    await page.route("**/api/chat/attachments/upload", async (route) => {
      const body = route.request().postDataBuffer()!;
      const start = body.indexOf(Buffer.from([0xff, 0xd8, 0xff]));
      const end = body.lastIndexOf(Buffer.from([0xff, 0xd9]));
      expect(start).toBeGreaterThan(0);
      expect(body.toString("latin1", 0, start)).toContain(
        'filename="noise.jpg"',
      );
      expect(body.toString("latin1", 0, start)).toContain("image/jpeg");
      uploadedJpeg = body.subarray(start, end + 2);
      await uploadGate;
      await route.fulfill({
        json: {
          object_key: "key",
          public_url: "https://example.com/noise.jpg",
        },
      });
    });
    await page.goto("/");
    const input = page.locator('input[type="file"]');
    await expect(input).toBeAttached();
    const presign = page.waitForRequest("**/api/chat/attachments/presign");
    const originalSize = await input.evaluate(async (element) => {
      const canvas = document.createElement("canvas");
      canvas.width = 3200;
      canvas.height = 1800;
      const ctx = canvas.getContext("2d")!;
      const pixels = ctx.createImageData(canvas.width, canvas.height);
      let seed = 181;
      for (let i = 0; i < pixels.data.length; i += 4) {
        seed = (Math.imul(seed, 1664525) + 1013904223) | 0;
        pixels.data[i] = seed & 255;
        pixels.data[i + 1] = (seed >>> 8) & 255;
        pixels.data[i + 2] = (seed >>> 16) & 255;
        pixels.data[i + 3] = 255;
      }
      ctx.putImageData(pixels, 0, 0);
      // A transparent corner verifies PNG -> JPEG fills white, not black.
      ctx.clearRect(0, 0, 80, 80);
      const blob = await new Promise<Blob>((resolve) =>
        canvas.toBlob((value) => resolve(value!), "image/png"),
      );
      const transfer = new DataTransfer();
      transfer.items.add(new File([blob], "noise.png", { type: "image/png" }));
      (element as HTMLInputElement).files = transfer.files;
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return blob.size;
    });
    expect(originalSize).toBeGreaterThan(4_000_000);
    await expect(page.getByText(/Optimizing/)).toBeVisible();
    const request = await presign;
    expect(request.postDataJSON()).toMatchObject({
      content_type: "image/jpeg",
      filename: "noise.jpg",
    });
    expect(request.postDataJSON().content_length).toBeLessThanOrEqual(
      4_400_000,
    );
    await expect(page.getByText("Uploading", { exact: true })).toBeVisible();
    await expect.poll(() => uploadedJpeg?.length).toBeGreaterThan(0);
    expect(uploadedJpeg!.length).toBe(request.postDataJSON().content_length);
    const decoded = await page.evaluate(async (base64) => {
      const blob = await (
        await fetch(`data:image/jpeg;base64,${base64}`)
      ).blob();
      const image = await createImageBitmap(blob);
      const canvas = document.createElement("canvas");
      canvas.width = image.width;
      canvas.height = image.height;
      const ctx = canvas.getContext("2d")!;
      ctx.drawImage(image, 0, 0);
      return {
        width: image.width,
        height: image.height,
        corner: [...ctx.getImageData(2, 2, 1, 1).data],
      };
    }, uploadedJpeg!.toString("base64"));
    expect(Math.max(decoded.width, decoded.height)).toBeLessThanOrEqual(2560);
    expect(decoded.corner.slice(0, 3).every((channel) => channel >= 250)).toBe(
      true,
    );
    releaseUpload();
    await expect(page.getByText("Ready", { exact: true })).toBeVisible();
    await expect(page.getByText("noise.jpg", { exact: true })).toBeVisible();
    if (corruptWorker) expect(workerRequests).toEqual([]);
    else {
      expect(workerRequests.length).toBeGreaterThan(0);
      expect(workerRequests.every((url) => url.startsWith("blob:"))).toBe(true);
    }
    expect(cdnRequests).toEqual([IMAGE_COMPRESSION_WORKER_URL]);
  });
}
