import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const { outputFiles } = await build({
  entryPoints: [fileURLToPath(new URL("../src/vault-sync-modal.ts", import.meta.url))],
  bundle: true,
  format: "esm",
  write: false,
  plugins: [{
    name: "mock-obsidian",
    setup(builder) {
      builder.onResolve({ filter: /^obsidian$/ }, () => ({ path: "obsidian", namespace: "mock" }));
      builder.onLoad({ filter: /.*/, namespace: "mock" }, () => ({ contents: `
        class Element {
          constructor({ text, cls, attr } = {}) {
            this.textContent = text;
            this.cls = cls;
            this.attrs = { ...attr };
            this.children = [];
          }
          createEl(_tag, options) { const child = new Element(options); this.children.push(child); return child; }
          createDiv(options) { return this.createEl("div", options); }
          createSpan(options) { return this.createEl("span", options); }
          setAttribute(key, value) { this.attrs[key] = value; }
          addEventListener(event, callback) { this[event] = callback; }
          addClass() {}
          empty() { this.children = []; }
        }
        export class Modal {
          constructor(app) { this.app = app; this.contentEl = new Element(); this.modalEl = new Element(); }
          setTitle() {}
          close() { this.onClose(); }
        }
        export class Notice {}
      ` }));
    },
  }],
});
const { VaultSyncModal } = await import(
  `data:text/javascript;base64,${Buffer.from(outputFiles[0].contents).toString("base64")}`
);

function note(name, changes = {}) {
  return {
    file: { name, path: name },
    audit: { identified: 1, found: 1, updates: 0, moves: 0, unchanged: 1, without_ids: 0, errors: [], identities: [], ...changes },
  };
}

test("filters audit notes by updates, deck moves, and ID issues", async () => {
  const shared = { kind: "marker", value: "ab1" };
  const opened = [];
  const app = { workspace: { getLeaf: () => ({ openFile: async (file) => opened.push(file.path) }) } };
  const modal = new VaultSyncModal(app, [
    note("Current.md"),
    note("Changed.md", { updates: 1 }),
    note("Moved.md", { updates: 1, moves: 1 }),
    note("Local.md", { without_ids: 1 }),
    note("Missing.md", { found: 0 }),
    note("Copy.md", { identities: [shared] }),
    note("Original.md", { identities: [shared] }),
    note("Repeated.md", { identities: [{ kind: "marker", value: "ab2" }, { kind: "marker", value: "ab2" }] }),
    note("Validation.md", { errors: ["Missing attachment"] }),
    { file: { name: "Unread.md", path: "Unread.md" }, error: "Could not read note" },
  ]);
  modal.onOpen();
  const [toolbar, results] = modal.contentEl.children;
  const [all, updates, moves, issues, current] = toolbar.children;
  const rows = () => results.children.filter((child) => child.cls === "corvus-vault-sync-note");
  const names = () => rows().map((row) => row.children[0].children[0].textContent);

  assert.deepEqual(toolbar.children.map((button) => button.textContent), [
    "All (10)", "Updates (2)", "Deck moves (1)", "ID issues (5)", "Up to date (1)",
  ]);
  assert.equal(all.attrs["aria-pressed"], "true");
  assert.equal(names()[0], "Unread.md");
  updates.click();
  assert.deepEqual(names(), ["Changed.md", "Moved.md"]);
  moves.click();
  assert.deepEqual(names(), ["Moved.md"]);
  assert.equal(updates.attrs["aria-pressed"], "false");
  issues.click();
  assert.deepEqual(names(), ["Copy.md", "Local.md", "Missing.md", "Original.md", "Repeated.md"]);
  current.click();
  assert.deepEqual(names(), ["Current.md"]);
  await rows()[0].click();
  assert.deepEqual(opened, ["Current.md"]);
});
