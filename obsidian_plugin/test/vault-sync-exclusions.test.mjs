import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const { outputFiles } = await build({
  entryPoints: [fileURLToPath(new URL("../src/vault-sync-exclusions.ts", import.meta.url))],
  bundle: true,
  format: "esm",
  write: false,
});
const { createVaultSyncExclusionMatcher } = await import(
  `data:text/javascript;base64,${Buffer.from(outputFiles[0].contents).toString("base64")}`
);

test("matches individual vault paths and entire folders without matching similarly named paths", () => {
  const isExcluded = createVaultSyncExclusionMatcher(
    " Templates/ \r\n Study\\Card Template.md \r\n\r\n./Archive/Old Templates\r\n",
  );

  assert.equal(isExcluded("Templates/Card.md"), true);
  assert.equal(isExcluded("Templates/Nested/Card.md"), true);
  assert.equal(isExcluded("Study/Card Template.md"), true);
  assert.equal(isExcluded("Archive/Old Templates/Card.md"), true);
  assert.equal(isExcluded("Templates backup/Card.md"), false);
  assert.equal(isExcluded("templates/Card.md"), false);
  assert.equal(isExcluded("Other/Card Template.md"), false);
  assert.equal(isExcluded("Study/Card Template 2.md"), false);
});

test("ignores empty lines and skips nothing by default", () => {
  const isExcluded = createVaultSyncExclusionMatcher("\n  \n");
  assert.equal(isExcluded("Templates/Card.md"), false);
  assert.equal(isExcluded("Card.md"), false);
});
