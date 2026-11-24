import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const { outputFiles } = await build({
  stdin: {
    contents: `
      export { prepareTikzAttachments } from "./src/tikz-attachments";
      export { collectNoteSource } from "./src/note-source";
      export { writeBackImportIds } from "./src/writeback";
      export { CorvusClient } from "./src/corvus-client";
      export { setResponse, TFile } from "obsidian";
    `,
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
          constructor(path) {
            this.path = path;
            this.name = path.split("/").at(-1);
            this.extension = this.name.split(".").at(-1);
            this.parent = { path: path.split("/").slice(0, -1).join("/") };
          }
        }
        export const normalizePath = (path) => path.replace(/^\\//, "");
        export const parseYaml = () => ({});
        let response;
        export function setResponse(value) { response = value; }
        export async function requestUrl() { return response; }
      ` }));
    },
  }],
});
const { prepareTikzAttachments, collectNoteSource, writeBackImportIds, CorvusClient, setResponse, TFile } = await import(
  `data:text/javascript;base64,${Buffer.from(outputFiles[0].contents).toString("base64")}`
);

const circuit = [
  "``` tikz", "\\usepackage{circuitikz}", "", "\\begin{document}",
  "\\begin{circuitikz}[american]", "  \\draw (0,0) to[R,l=$2$] (2,0);", "\\end{circuitikz}",
  "\\end{document}", "```",
];
const png = Uint8Array.of(137, 80, 78, 71).buffer;

test("replaces real TikZ blocks once per source and keeps original card marker lines", async () => {
  const lines = ["Circuit", "#long-card", ...circuit, "", "Explanation", "", "Two ways", "#card-reverse", "Answer", "", ...circuit];
  const original = lines.join("\r\n");
  const calls = [];
  const prepared = await prepareTikzAttachments(original, "Circuit.md", async (markdown) => { calls.push(markdown); return png; });
  const [path] = prepared.images.keys();
  assert.match(path, /^__corvus_tikz__\/tikz-[0-9a-f]{64}\.png$/);
  assert.equal(prepared.images.size, 1);
  assert.equal(calls.length, 1);
  assert.match(calls[0], /^```tikz\n/);
  assert.equal(prepared.content.includes("\\usepackage"), false);
  assert.ok(prepared.content.includes(`![TikZ diagram](${path})\r\n\r\nExplanation`));

  const serverMarkerLine = prepared.content.split("\r\n").indexOf("#card-reverse") + 1;
  const originalMarkerLine = lines.indexOf("#card-reverse") + 1;
  const sourceHash = Buffer.from(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(original))).toString("hex");
  setResponse({ status: 201, headers: {}, text: JSON.stringify({
    source_hash: sourceHash,
    cards: [0, 1].map((index) => ({ index, marker_line: serverMarkerLine, will_write_back_id: true })),
  }) });
  const client = new CorvusClient({ baseUrl: "https://corvus.example", rootDeckPath: "STEM" });
  const preview = await client.createPreview({ ...prepared, path: "Circuit.md", sourceHash, attachments: [] });
  assert.deepEqual(preview.cards.map((card) => card.marker_line), [originalMarkerLine, originalMarkerLine]);

  let updated;
  const app = { vault: { cachedRead: async () => original, process: async (_, update) => { updated = update(original); } } };
  const count = await writeBackImportIds(app, {}, preview, { cards: [
    { index: 0, marker_line: serverMarkerLine, marker_kind: "card", import_id: "a1" },
    { index: 1, marker_line: serverMarkerLine, marker_kind: "reverse", import_id: "a2" },
  ] });
  assert.equal(count, 1);
  assert.equal(updated, lines.map((line, i) => i === originalMarkerLine - 1 ? "#card-reverse id:a1|a2" : line).join("\r\n"));
});

test("fingerprints each TikZ source even when the rendered bytes are the same", async () => {
  const first = circuit.join("\n");
  const second = first.replace("$2$", "$3$");
  const prepared = await prepareTikzAttachments(`${first}\n${second}`, "Circuits.md", async () => png);
  const paths = [...prepared.images.keys()];
  assert.equal(paths.length, 2);
  assert.deepEqual(prepared.content.split("\n"), paths.map((path) => `![TikZ diagram](${path})`));
});

test("leaves TeX math and fenced Markdown examples intact and reports renderer errors with line numbers", async () => {
  const example = ["An equation $x^2$", "````markdown", ...circuit, "````"].join("\n");
  const untouched = await prepareTikzAttachments(example, "Example.md", async () => { throw Error("unexpected render"); });
  assert.equal(untouched.content, example);
  assert.equal(untouched.originalLineNumbers, undefined);
  assert.equal(untouched.images.size, 0);

  await assert.rejects(
    prepareTikzAttachments(["# Circuit", ...circuit].join("\n"), "Circuit.md", async () => { throw Error("renderer unavailable"); }),
    /TikZ in Circuit\.md at line 2: renderer unavailable/,
  );
  await assert.rejects(
    prepareTikzAttachments(circuit.slice(0, -1).join("\n"), "Circuit.md", async () => png),
    /Unclosed TikZ block in Circuit\.md at line 1/,
  );
});

test("ID writeback preserves the note's line endings and existing reverse ID", async () => {
  const original = "Title\r\n\nQuestion #card-reverse id:|ab\r\nAnswer\n";
  const sourceHash = Buffer.from(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(original))).toString("hex");
  let updated;
  const app = { vault: { cachedRead: async () => original, process: async (_, update) => { updated = update(original); } } };
  const preview = { source_hash: sourceHash, cards: [{ index: 0, marker_line: 3, marker_column: 9, will_write_back_id: true }] };

  await writeBackImportIds(app, {}, preview, { cards: [{ index: 0, marker_kind: "card", import_id: "cd" }] });
  assert.equal(updated, "Title\r\n\nQuestion #card-reverse id:cd|ab\r\nAnswer\n");
});

test("rewrites only real image destinations and adds IDs at the original marker after attachment and TikZ edits", async () => {
  const question = '💡 What does `#card id:1a` mean? ![Circuit](<Images/diagram final.png> "Layout") #card-reverse id:beef';
  const lines = ["---", "title: Circuit", "---", ...circuit, "", question, "![Alternate](Images/circuit(1).png)", "",
    "```md", "![[Images/literal.png]]", "```", "", "> ```md", "> ![[Images/literal.png]]", "> ```", "", "`![Literal](Images/literal.png)`"];
  const original = lines.join("\r\n");
  const assets = new Map(["diagram final.png", "circuit(1).png", "literal.png"].map((name) => {
    const path = `Notes/Images/${name}`;
    return [path, new TFile(path)];
  }));
  let updated;
  const app = {
    vault: {
      getAbstractFileByPath: (path) => assets.get(path),
      readBinary: async () => png,
      cachedRead: async () => original,
      process: async (_, update) => { updated = update(original); },
    },
    metadataCache: { getFirstLinkpathDest: (path) => assets.get(`Notes/${path}`) },
  };
  const file = new TFile("Notes/Circuit.md");
  const tikz = await prepareTikzAttachments(original, file.path, async () => png);
  const source = await collectNoteSource(app, file, tikz.content);
  const attachmentPaths = source.attachments.map(({ path }) => path);
  assert.deepEqual(attachmentPaths, ["Notes/Images/diagram final.png", "Notes/Images/circuit(1).png"]);
  assert.ok(source.content.includes('![Circuit](</Notes/Images/diagram%20final.png> "Layout")'));
  assert.ok(source.content.includes("![[Images/literal.png]]"));
  assert.ok(source.content.includes("`![Literal](Images/literal.png)`"));

  const serverLine = source.content.split("\r\n").findIndex((line) => line.includes("#card-reverse")) + 1;
  const serverColumn = source.content.split("\r\n")[serverLine - 1].indexOf("#card-reverse");
  const sourceHash = Buffer.from(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(original))).toString("hex");
  setResponse({ status: 201, headers: {}, text: JSON.stringify({
    source_hash: sourceHash,
    cards: [{ index: 1, marker_line: serverLine, marker_column: serverColumn, will_write_back_id: true }],
  }) });
  const client = new CorvusClient({ baseUrl: "https://corvus.example", rootDeckPath: "STEM" });
  const originalSource = { ...source, sourceHash, originalLineNumbers: tikz.originalLineNumbers };
  const preview = await client.createPreview(originalSource);
  assert.equal(preview.cards[0].marker_line, lines.indexOf(question) + 1);
  assert.equal(preview.cards[0].marker_column, question.indexOf("#card-reverse"));
  await writeBackImportIds(app, file, preview, { cards: [{ index: 1, marker_kind: "reverse", import_id: "bef0" }] });
  assert.equal(updated, original.replace(`${question}\r\n`, `${question}|bef0\r\n`));

  app.vault.process = async (_, update) => { updated = update(`${original}\r\nAn unsynced edit.`); };
  await assert.rejects(
    writeBackImportIds(app, file, preview, { cards: [{ index: 1, marker_kind: "reverse", import_id: "bef0" }] }),
    /note changed before their IDs could be written/,
  );

  setResponse({ status: 200, headers: {}, text: JSON.stringify({
    errors: ["Missing answer."], error_locations: [{ marker_line: serverLine, errors: ["Missing answer."] }],
  }) });
  const audit = await client.auditNote(originalSource);
  assert.deepEqual(audit.errors, [`Line ${lines.indexOf(question) + 1} · Missing answer.`]);
});
