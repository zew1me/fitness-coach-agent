import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

type VercelRoute = {
  destination: string;
  source: string;
};

type VercelConfig = {
  rewrites?: VercelRoute[];
};

describe("vercel routing", () => {
  const config = JSON.parse(
    readFileSync(new URL("../../vercel.json", import.meta.url), "utf8"),
  ) as VercelConfig;

  function rewrittenDestination(path: string): string | undefined {
    return config.rewrites?.find((rewrite) =>
      matchesSource(rewrite.source, path),
    )?.destination;
  }

  function matchesSource(source: string, path: string): boolean {
    if (source === path) {
      return true;
    }
    if (source.endsWith("/(.*)")) {
      const prefix = source.slice(0, -"(.*)".length);
      return path.startsWith(prefix);
    }
    if (!source.includes(":path*")) {
      return false;
    }

    const prefix = source.replace(":path*", "");
    return path === prefix.slice(0, -1) || path.startsWith(prefix);
  }

  it("routes browser-session and browser-token requests to the FastAPI entrypoint", () => {
    expect(config.rewrites).toEqual(
      expect.arrayContaining([
        { source: "/api/oauth/:path*", destination: "/api/index.py" },
        { source: "/api/engine/:path*", destination: "/api/index.py" },
        { source: "/api/files/:path*", destination: "/api/index.py" },
        { source: "/api/intervals/:path*", destination: "/api/index.py" },
        { source: "/api/strava/:path*", destination: "/api/index.py" },
        { source: "/api/chat/(.*)", destination: "/api/index.py" },
      ]),
    );
    expect(rewrittenDestination("/api/oauth/browser-token")).toBe(
      "/api/index.py",
    );
    expect(rewrittenDestination("/api/oauth/browser-session")).toBe(
      "/api/index.py",
    );
    expect(rewrittenDestination("/api/engine/get-athlete-summary")).toBe(
      "/api/index.py",
    );
    expect(rewrittenDestination("/api/intervals/authorize")).toBe(
      "/api/index.py",
    );
    expect(rewrittenDestination("/api/intervals/callback")).toBe(
      "/api/index.py",
    );
    expect(rewrittenDestination("/api/strava/authorize")).toBe("/api/index.py");
    expect(rewrittenDestination("/api/strava/callback")).toBe("/api/index.py");
    expect(rewrittenDestination("/api/chat/thread")).toBe("/api/index.py");
    expect(rewrittenDestination("/api/chat/messages")).toBe("/api/index.py");
    expect(rewrittenDestination("/api/chat/attachments/presign")).toBe(
      "/api/index.py",
    );
    expect(rewrittenDestination("/api/chat/model-state")).toBe("/api/index.py");
    expect(rewrittenDestination("/api/chat/model-state/lease")).toBe(
      "/api/index.py",
    );
  });

  it("does not rewrite the exact Next.js chat streaming route", () => {
    expect(rewrittenDestination("/api/chat")).toBeUndefined();
  });
});
