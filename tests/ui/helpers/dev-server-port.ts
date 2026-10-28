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

interface ResolveDevServerOptions {
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

interface DevServerTarget {
  port: number;
  /**
   * Whether Playwright may reuse a server already listening on `port`. False
   * for hash-derived worktree ports: two worktrees can hash to the same port,
   * and reusing would silently test the other worktree's branch, so a
   * collision must fail loudly ("port already used") instead.
   */
  reuseExistingServer: boolean;
}

/**
 * Pick the port Playwright's dev server listens on, and whether an existing
 * server on that port may be reused.
 *
 * Every checkout used to share port 3000, and `reuseExistingServer` meant a
 * Playwright run in one worktree silently tested another worktree's dev server
 * (issue #202). Resolution order:
 *
 * 1. `PLAYWRIGHT_PORT`, when set, always wins; reuse stays on because the
 *    caller chose the port deliberately.
 * 2. A linked worktree gets a stable port derived from its absolute path, so
 *    concurrent runs in different worktrees use different servers. Reuse is
 *    off so a rare hash collision fails instead of crossing worktrees.
 * 3. The primary checkout keeps 3000 with reuse, so CI and an already-running
 *    `bun dev` behave exactly as before.
 */
export function resolveDevServer({
  override,
  rootDir,
  isLinkedWorktree = defaultIsLinkedWorktree,
}: ResolveDevServerOptions): DevServerTarget {
  const trimmed = override?.trim();
  if (trimmed) {
    const port = Number(trimmed);
    if (!Number.isInteger(port) || port < 1 || port > 65_535) {
      throw new Error(
        `PLAYWRIGHT_PORT must be an integer between 1 and 65535, got "${override}"`,
      );
    }
    return { port, reuseExistingServer: true };
  }
  return isLinkedWorktree(rootDir)
    ? { port: worktreePort(rootDir), reuseExistingServer: false }
    : { port: DEFAULT_DEV_SERVER_PORT, reuseExistingServer: true };
}
