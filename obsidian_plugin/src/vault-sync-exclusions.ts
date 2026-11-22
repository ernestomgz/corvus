export function createVaultSyncExclusionMatcher(exclusions: string): (path: string) => boolean {
  const paths = exclusions.split(/\r?\n/)
    .map((line) => line.trim().replace(/\\/g, "/").replace(/^(?:\.\/|\/)+|\/+$/g, ""))
    .filter(Boolean);

  return (path) => paths.some((excluded) => path === excluded || path.startsWith(`${excluded}/`));
}
