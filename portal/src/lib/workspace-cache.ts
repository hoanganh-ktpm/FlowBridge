// SPDX-License-Identifier: GPL-3.0-or-later
export const workspaceCachePrefix = 'flowbridge.workspace.v1:';
export const workspaceStaleEvent = 'flowbridge:workspace-stale';
export function clearWorkspaceCache() {
  try { for (const key of Object.keys(sessionStorage)) if (key.startsWith(workspaceCachePrefix)) sessionStorage.removeItem(key); } catch { /* Storage may be disabled. */ }
}
