import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_DEV_SERVER_PORT,
  resolveDevServerPort,
} from "../ui/helpers/dev-server-port";

const linked = (): boolean => true;
const primary = (): boolean => false;

describe("resolveDevServerPort", () => {
  it("keeps 3000 for the primary checkout", () => {
    expect(
      resolveDevServerPort({ rootDir: "/repo", isLinkedWorktree: primary }),
    ).toBe(DEFAULT_DEV_SERVER_PORT);
  });

  it("gives a linked worktree a stable port off 3000", () => {
    const first = resolveDevServerPort({
      rootDir: "/repo/.worktrees/a",
      isLinkedWorktree: linked,
    });
    const again = resolveDevServerPort({
      rootDir: "/repo/.worktrees/a",
      isLinkedWorktree: linked,
    });
    expect(first).toBe(again);
    expect(first).toBeGreaterThanOrEqual(3100);
    expect(first).toBeLessThan(4000);
  });

  it("gives different worktrees different ports", () => {
    const ports = new Set(
      ["a", "b", "c", "d"].map((name) =>
        resolveDevServerPort({
          rootDir: `/repo/.worktrees/${name}`,
          isLinkedWorktree: linked,
        }),
      ),
    );
    expect(ports.size).toBe(4);
  });

  it("lets PLAYWRIGHT_PORT override both defaults", () => {
    expect(
      resolveDevServerPort({
        override: " 4321 ",
        rootDir: "/repo/.worktrees/a",
        isLinkedWorktree: linked,
      }),
    ).toBe(4321);
    expect(
      resolveDevServerPort({
        override: "4321",
        rootDir: "/repo",
        isLinkedWorktree: primary,
      }),
    ).toBe(4321);
  });

  it("ignores an empty PLAYWRIGHT_PORT", () => {
    expect(
      resolveDevServerPort({
        override: "",
        rootDir: "/repo",
        isLinkedWorktree: primary,
      }),
    ).toBe(DEFAULT_DEV_SERVER_PORT);
  });

  it.each(["abc", "0", "70000", "30.5"])(
    "rejects an invalid PLAYWRIGHT_PORT (%s)",
    (override) => {
      expect(() =>
        resolveDevServerPort({ override, rootDir: "/repo" }),
      ).toThrow(/PLAYWRIGHT_PORT/);
    },
  );
});

describe("resolveDevServerPort linked-worktree detection", () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("treats a .git file as a linked worktree", () => {
    dir = mkdtempSync(path.join(tmpdir(), "pw-port-"));
    writeFileSync(path.join(dir, ".git"), "gitdir: /repo/.git/worktrees/x\n");
    expect(resolveDevServerPort({ rootDir: dir })).not.toBe(
      DEFAULT_DEV_SERVER_PORT,
    );
  });

  it("treats a .git directory as the primary checkout", () => {
    dir = mkdtempSync(path.join(tmpdir(), "pw-port-"));
    mkdirSync(path.join(dir, ".git"));
    expect(resolveDevServerPort({ rootDir: dir })).toBe(
      DEFAULT_DEV_SERVER_PORT,
    );
  });

  it("falls back to 3000 when there is no .git at all", () => {
    dir = mkdtempSync(path.join(tmpdir(), "pw-port-"));
    expect(resolveDevServerPort({ rootDir: dir })).toBe(
      DEFAULT_DEV_SERVER_PORT,
    );
  });
});
