import { sha256Hex } from "./hash";
import { findMarkdownFences, frontmatterEnd } from "./markdown-syntax";

type RenderTikzImage = (markdown: string, digest: string) => Promise<ArrayBuffer>;

export async function prepareTikzAttachments(content: string, notePath: string, render: RenderTikzImage): Promise<{
  content: string;
  originalLineNumbers?: number[];
  images: Map<string, ArrayBuffer>;
}> {
  const lines = content.split(/\r?\n/);
  const blocks = findMarkdownFences(lines, frontmatterEnd(lines) + 1)
    .filter(({ fence, info, inContainer }) => !inContainer && fence[0] === "`" && /^tikz$/i.test(info));
  const images = new Map<string, ArrayBuffer>();
  if (blocks.length === 0) return { content, images };

  const prepared: string[] = [];
  const originalLineNumbers: number[] = [];
  let cursor = 0;
  for (const { start, end, fence, indent, closed } of blocks) {
    if (!closed) throw new Error(`Unclosed TikZ block in ${notePath} at line ${start + 1}.`);
    while (cursor < start) {
      prepared.push(lines[cursor]);
      originalLineNumbers.push(++cursor);
    }

    const source = lines.slice(start + 1, end)
      .map((line) => line.replace(/^ {0,3}/, (spaces) => spaces.slice(indent.length)))
      .join("\n");
    const digest = await sha256Hex(source);
    const path = `__corvus_tikz__/tikz-${digest}.png`;
    if (!images.has(path)) {
      try {
        images.set(path, await render(`${fence}tikz\n${source}\n${fence}`, digest));
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        throw new Error(`TikZ in ${notePath} at line ${start + 1}: ${reason}`);
      }
    }
    prepared.push(`${indent}![TikZ diagram](${path})`);
    originalLineNumbers.push(start + 1);
    cursor = end + 1;
  }

  while (cursor < lines.length) {
    prepared.push(lines[cursor]);
    originalLineNumbers.push(++cursor);
  }
  return { content: prepared.join(content.includes("\r\n") ? "\r\n" : "\n"), originalLineNumbers, images };
}
