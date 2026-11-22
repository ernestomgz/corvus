import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const bundle = await build({
  entryPoints: [fileURLToPath(new URL("../src/vault-sync-conflicts.ts", import.meta.url))],
  bundle: true,
  format: "esm",
  write: false,
});
const moduleUrl = `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].contents).toString("base64")}`;
const { findVaultIdConflicts } = await import(moduleUrl);

function note(path, markers = [], anchors = []) {
  return {
    path,
    audit: {
      identities: [
        ...markers.map((value) => ({ kind: "marker", value })),
        ...anchors.map((value) => ({ kind: "anchor", value })),
      ],
    },
  };
}

test("flags all notes with a shared card ID, including a reverse-card ID", () => {
  const conflicts = findVaultIdConflicts([
    note("Original.md", ["ab1", "ab2"]),
    note("Copies/Copy.md", ["ab2", "ab2"]),
    note("Other.md", ["ab2"]),
  ]);

  assert.deepEqual([...conflicts], [
    ["Copies/Copy.md", ["Duplicate id:ab2 also appears in Original.md, Other.md"]],
    ["Original.md", ["Duplicate id:ab2 also appears in Copies/Copy.md, Other.md"]],
    ["Other.md", ["Duplicate id:ab2 also appears in Copies/Copy.md, Original.md"]],
  ]);
});

test("separates legacy anchors and marker IDs and ignores repeats within one note", () => {
  const conflicts = findVaultIdConflicts([
    note("Original.md", ["ab1", "ab1"]),
    note("Legacy.md", [], ["ab1"]),
    note("Legacy copy.md", [], ["ab1"]),
  ]);

  assert.equal(conflicts.has("Original.md"), false);
  assert.deepEqual(conflicts.get("Legacy.md"), ["Duplicate id::ab1 also appears in Legacy copy.md"]);
  assert.deepEqual(conflicts.get("Legacy copy.md"), ["Duplicate id::ab1 also appears in Legacy.md"]);
});
