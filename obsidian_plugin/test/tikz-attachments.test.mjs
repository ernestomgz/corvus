import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const { outputFiles } = await build({
  stdin: {
    contents: `
      export { prepareTikzAttachments } from "./src/tikz-attachments";
      export { writeBackImportIds } from "./src/writeback";
      export { CorvusClient } from "./src/corvus-client";
      export { setResponse } from "obsidian";
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
        let response;
        export function setResponse(value) { response = value; }
        export async function requestUrl() { return response; }
      ` }));
    },
  }],
});
const { prepareTikzAttachments, writeBackImportIds, CorvusClient, setResponse } = await import(
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
  const app = { vault: { cachedRead: async () => original, modify: async (_, content) => { updated = content; } } };
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
