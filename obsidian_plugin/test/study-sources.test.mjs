import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const { outputFiles } = await build({
  stdin: {
    contents: 'export { collectCurrentStudySetSource } from "./src/note-source"; export { TFile, setYaml } from "obsidian";',
    resolveDir: fileURLToPath(new URL("../", import.meta.url)),
  },
  bundle: true,
  format: "esm",
  write: false,
  plugins: [{
    name: "mock-obsidian",
    setup(builder) {
      builder.onResolve({ filter: /^obsidian$/ }, () => ({ path: "obsidian", namespace: "mock" }));
      builder.onLoad({ filter: /.*/, namespace: "mock" }, () => ({ contents: `
        export class App {}
        export class Component {}
        export class MarkdownRenderer {}
        export class TFile {
          constructor(path) { this.path = path; this.basename = path.split("/").at(-1).slice(0, -3); this.extension = "md"; }
        }
        export const normalizePath = (path) => path;
        let fixtures;
        export function setYaml(value) { fixtures = value; }
        export function parseYaml(text) { return fixtures[text]; }
      ` }));
    },
  }],
});

const { collectCurrentStudySetSource, TFile, setYaml } = await import(
  `data:text/javascript;base64,${Buffer.from(outputFiles[0].contents).toString("base64")}`
);

function appFor(fields, companions = {}) {
  const preset = new TFile("BJT practice.md");
  const main = new TFile("Electronics/BJT.md");
  const files = new Map([["BJT", main]]);
  const markdown = new Map([[preset.path, "[[BJT]]"]]);
  const fixtureYaml = { 'title: BJT': fields };
  markdown.set(main.path, "---\ntitle: BJT\n---");
  for (const [name, companionFields] of Object.entries(companions)) {
    const file = new TFile(`Electronics/${name}.md`);
    files.set(name, file);
    markdown.set(file.path, `---\ntitle: ${name}\n---`);
    fixtureYaml[`title: ${name}`] = companionFields;
  }
  setYaml(fixtureYaml);
  return {
    workspace: { getActiveFile: () => preset },
    vault: { cachedRead: async (file) => markdown.get(file.path) },
    metadataCache: {
      getFileCache: (file) => file === preset ? { links: [{ original: "[[BJT]]", link: "BJT" }] } : null,
      getFirstLinkpathDest: (name, from) => name === "BJT" || from === main.path ? files.get(name) : null,
    },
  };
}

test("follows optional exercise and question links once and honors explicit custom types", async () => {
  const app = appFor({
    "questions-source": "[[Questions]]",
    "exercises-source": ["[[Exercises]]", "[[Exam|Practice]]", "[[Exercises]]"],
  }, {
    Questions: { "exercises-source": "[[Skip nested link]]" },
    Exercises: {},
    Exam: { "note-type": "Exam practice" },
  });
  const source = await collectCurrentStudySetSource(app);
  assert.deepEqual(source.errors, []);
  assert.deepEqual(source.links.map(({ obsidianPath, noteType, noteTypeExplicit }) => [obsidianPath, noteType, noteTypeExplicit]), [
    ["Electronics/BJT.md", undefined, false],
    ["Electronics/Questions.md", "questions", false],
    ["Electronics/Exercises.md", "exercises", false],
    ["Electronics/Exam.md", "exam-practice", true],
  ]);
});

test("reports broken and ambiguous companion links and supports a note without companion fields", async () => {
  const plain = await collectCurrentStudySetSource(appFor({}));
  assert.deepEqual(plain.errors, []);
  assert.equal(plain.links.length, 1);

  const invalid = await collectCurrentStudySetSource(appFor({
    "questions-source": "[[Questions]]",
    "exercises-source": ["[[Questions]]", "[[Missing]]", "[[Questions#Section]]"],
  }, { Questions: {} }));
  assert.equal(invalid.links.length, 2);
  assert.equal(invalid.errors.length, 3);
  assert.ok(invalid.errors.some((error) => /linked as both questions and exercises/.test(error)));
  assert.ok(invalid.errors.some((error) => /BJT.md → exercises-source: Link not found/.test(error)));
  assert.ok(invalid.errors.some((error) => /must point to a whole Markdown note/.test(error)));
});
