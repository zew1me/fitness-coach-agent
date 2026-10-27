import { createHash } from "node:crypto";
import { statSync } from "node:fs";
import path from "node:path";

/** Port the primary checkout's dev server uses, matching `next dev`'s default. */
export const DEFAULT_DEV_SERVER_PORT = 3000;

/**
 * Linked worktrees get a port from this range so they never land on the primary
 * checkout's 3000. 900 slots keep a collision between two worktrees unlikely
 * while staying clear of the backend's 8001 used by `dev:local`.
 */
const WORKTREE_PORT_BASE = 3100;
const WORKTREE_PORT_SPAN = 900;

interface ResolveDevServerPortOptions {
  /** Explicit override, normally `process.env["PLAYWRIGHT_PORT"]`. */
  override?: string | undefined;
  /** Repository root the Playwright config lives in. */
  rootDir: string;
  /** Injected for tests; defaults to checking whether `<rootDir>/.git` is a file. */
  isLinkedWorktree?: (rootDir: string) => boolean;
}

/**
 * A linked worktree (`git worktree add`) has a `.git` *file* pointing at the
 * shared git dir; the primary checkout has a `.git` directory.
 */
function defaultIsLinkedWorktree(rootDir: string): boolean {
  try {
    return statSync(path.join(rootDir, ".git")).isFile();
  } catch {
    return false;
  }
}

function worktreePort(rootDir: string): number {
  const digest = createHash("sha256").update(path.resolve(rootDir)).digest();
  return WORKTREE_PORT_BASE + (digest.readUInt32BE(0) % WORKTREE_PORT_SPAN);
}

/**
 * Pick the port Playwright's dev server listens on.
 *
 * Every checkout used to share port 3000, and `reuseExistingServer` meant a
 * Playwright run in one worktree silently tested another worktree's dev server
 * (issue #202). Resolution order:
 *
 * 1. `PLAYWRIGHT_PORT`, when set, always wins.
 * 2. A linked worktree gets a stable port derived from its absolute path, so
 *    concurrent runs in different worktrees use different servers.
 * 3. The primary checkout keeps 3000, so CI and an already-running `bun dev`
 *    behave exactly as before.
 */
export function resolveDevServerPort({
  override,
  rootDir,
  isLinkedWorktree = defaultIsLinkedWorktree,
}: ResolveDevServerPortOptions): number {
  const trimmed = override?.trim();
  if (trimmed) {
    const port = Number(trimmed);
    if (!Number.isInteger(port) || port < 1 || port > 65_535) {
      throw new Error(
        `PLAYWRIGHT_PORT must be an integer between 1 and 65535, got "${override}"`,
      );
    }
    return port;
  }
  return isLinkedWorktree(rootDir)
    ? worktreePort(rootDir)
    : DEFAULT_DEV_SERVER_PORT;
}
