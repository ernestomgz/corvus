import type { NoteSyncAudit } from "./types";

export function findVaultIdConflicts(notes: { path: string; audit: NoteSyncAudit }[]): Map<string, string[]> {
  const pathsByIdentity = new Map<string, Set<string>>();
  for (const { path, audit } of notes) {
    for (const { kind, value } of audit.identities) {
      const identity = `${kind === "marker" ? "id:" : "id::"}${value}`;
      const paths = pathsByIdentity.get(identity) ?? new Set<string>();
      paths.add(path);
      pathsByIdentity.set(identity, paths);
    }
  }

  const conflicts = new Map<string, string[]>();
  for (const [identity, paths] of pathsByIdentity) {
    if (paths.size < 2) continue;
    const allPaths = [...paths].sort((a, b) => a.localeCompare(b));
    for (const path of allPaths) {
      const otherPaths = allPaths.filter((other) => other !== path);
      const messages = conflicts.get(path) ?? [];
      messages.push(`Duplicate ${identity} also appears in ${otherPaths.join(", ")}`);
      conflicts.set(path, messages);
    }
  }
  return conflicts;
}
