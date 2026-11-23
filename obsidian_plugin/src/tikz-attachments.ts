import { sha256Hex } from "./hash";

type RenderTikzImage = (markdown: string, digest: string) => Promise<ArrayBuffer>;

interface TikzBlock {
  start: number;
  end: number;
  fence: string;
  indent: string;
}

function findTikzBlocks(lines: string[], notePath: string): TikzBlock[] {
  const blocks: TikzBlock[] = [];
  const frontmatterEnd = lines[0]?.trim() === "---"
    ? lines.findIndex((line, index) => index > 0 && /^(---|\.\.\.)\s*$/.test(line))
    : -1;
  let open: Omit<TikzBlock, "end"> & { tikz: boolean } | undefined;

  for (let index = frontmatterEnd + 1; index < lines.length; index += 1) {
    const match = /^([ ]{0,3})(`{3,}|~{3,})(.*)$/.exec(lines[index]);
    if (!match) continue;
    const [, indent, fence, info] = match;
    if (open) {
      if (fence[0] !== open.fence[0] || fence.length < open.fence.length || info.trim()) continue;
      if (open.tikz) blocks.push({ start: open.start, end: index, fence: open.fence, indent: open.indent });
      open = undefined;
    } else if (fence[0] !== "`" || !info.includes("`")) {
      open = { start: index, fence, indent, tikz: fence[0] === "`" && /^tikz$/i.test(info.trim()) };
    }
  }

  if (open?.tikz) throw new Error(`Unclosed TikZ block in ${notePath} at line ${open.start + 1}.`);
  return blocks;
}

export async function prepareTikzAttachments(content: string, notePath: string, render: RenderTikzImage): Promise<{
  content: string;
  originalLineNumbers?: number[];
  images: Map<string, ArrayBuffer>;
}> {
  const lines = content.split(/\r?\n/);
  const blocks = findTikzBlocks(lines, notePath);
  const images = new Map<string, ArrayBuffer>();
  if (blocks.length === 0) return { content, images };

  const prepared: string[] = [];
  const originalLineNumbers: number[] = [];
  let cursor = 0;
  for (const { start, end, fence, indent } of blocks) {
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
