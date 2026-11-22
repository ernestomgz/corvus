import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const { outputFiles } = await build({
  stdin: {
    contents: 'export { CorvusClient } from "./src/corvus-client"; export { setResponse } from "obsidian";',
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
const { CorvusClient, setResponse } = await import(
  `data:text/javascript;base64,${Buffer.from(outputFiles[0].contents).toString("base64")}`
);

const client = new CorvusClient({ baseUrl: "https://corvus.example", rootDeckPath: "STEM" });
const note = { path: "Circuit.md", content: "", sourceHash: "hash", attachments: [] };

test("reports an HTML response with its HTTP status, route, and note", async () => {
  for (const status of [404, 200]) {
    setResponse({ status, headers: {}, text: "<!DOCTYPE html><title>Proxy error</title>" });
    await assert.rejects(client.auditNote(note), {
      message: `POST /api/v1/obsidian/audit-note returned HTTP ${status} (HTML). Check the Corvus server or proxy logs. (note: Circuit.md)`,
    });
  }
});

test("keeps JSON error details and returns successful JSON responses", async () => {
  setResponse({ status: 400, headers: {}, text: '{"error":"root deck not found"}' });
  await assert.rejects(client.auditNote(note), /HTTP 400: root deck not found/);

  setResponse({ status: 200, headers: {}, text: '{"identified":3}' });
  assert.deepEqual(await client.auditNote(note), { identified: 3 });
});
