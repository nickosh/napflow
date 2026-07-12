import { rmSync } from "node:fs";

/**
 * Build an idempotent workspace cleanup operation whose failed removal can
 * be retried. Windows can retain a child process's cwd briefly after the
 * process begins shutting down, so a failed attempt must not set `cleaned`.
 *
 * Dependency injection keeps the retry state directly testable without
 * relying on platform-specific file-lock behavior.
 */
export function createWorkspaceCleanup(
  workspace,
  {
    remove = rmSync,
    report = (message, error) => console.error(message, error),
  } = {},
) {
  let cleaned = false;
  return function cleanupWorkspace() {
    if (cleaned) return true;
    try {
      remove(workspace, { recursive: true, force: true });
      cleaned = true;
      return true;
    } catch (error) {
      report(`could not clean e2e workspace ${workspace}:`, error);
      return false;
    }
  };
}
