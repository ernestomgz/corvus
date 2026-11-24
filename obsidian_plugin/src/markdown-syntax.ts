export interface MarkdownFence {
  start: number;
  end: number;
  fence: string;
  indent: string;
  info: string;
  closed: boolean;
  inContainer: boolean;
}

export function findMarkdownFences(lines: string[], startLine: number): MarkdownFence[] {
  const blocks: MarkdownFence[] = [];
  let open: (Omit<MarkdownFence, "end" | "closed"> & { quoteDepth: number }) | undefined;
  for (let index = startLine; index < lines.length; index += 1) {
    const quote = /^(?: {0,3}>[ \t]?)+/.exec(lines[index])?.[0] ?? "";
    const quoteDepth = quote.split(">").length - 1;
    if (open && quoteDepth < open.quoteDepth) {
      blocks.push({ ...open, end: index - 1, closed: false });
      open = undefined;
    }
    if (open && quoteDepth > open.quoteDepth) continue;
    const line = lines[index].slice(quote.length);
    const list = !open ? /^ {0,3}(?:[-+*]|\d{1,9}[.)])[ \t]+/.exec(line)?.[0] ?? "" : "";
    const match = /^( {0,3})(`{3,}|~{3,})(.*)$/.exec(line.slice(list.length));
    if (!match) continue;
    const [, indent, fence, info] = match;
    if (open) {
      if (fence[0] !== open.fence[0] || fence.length < open.fence.length || info.trim()) continue;
      blocks.push({ ...open, end: index, closed: true });
      open = undefined;
    } else if (fence[0] !== "`" || !info.includes("`")) {
      open = { start: index, fence, indent, info: info.trim(), inContainer: !!(quote || list), quoteDepth };
    }
  }
  if (open) blocks.push({ ...open, end: lines.length - 1, closed: false });
  return blocks;
}

export function frontmatterEnd(lines: string[]): number {
  return lines[0]?.trim() === "---"
    ? lines.findIndex((line, index) => index > 0 && /^(---|\.\.\.)\s*$/.test(line))
    : -1;
}

export function isEscaped(text: string, start: number): boolean {
  let slashes = 0;
  while (start > 0 && text[--start] === "\\") slashes += 1;
  return slashes % 2 === 1;
}

function maskInlineCode(text: string, mask: Uint8Array, offset: number): void {
  const runs = [...text.matchAll(/`+/g)];
  const nextByLength = new Map<number, number>();
  const closers = new Array<number | undefined>(runs.length);
  for (let index = runs.length - 1; index >= 0; index -= 1) {
    const length = runs[index][0].length;
    const escaped = isEscaped(text, runs[index].index!);
    closers[index] = nextByLength.get(length - Number(escaped));
    nextByLength.set(length, index);
  }
  for (let index = 0; index < runs.length; index += 1) {
    const close = closers[index];
    if (close === undefined) continue;
    const start = runs[index].index!;
    const finish = runs[close].index!;
    if (/\r?\n[ \t]*\r?\n/.test(text.slice(start, finish))) continue;
    mask.fill(1, offset + start, offset + finish + runs[close][0].length);
    index = close;
  }
}

export function markdownCodeMask(text: string): Uint8Array {
  const mask = new Uint8Array(text.length);
  const lines = text.split(/(?<=\n)/);
  const lineText = lines.map((line) => line.replace(/\r?\n$/, ""));
  const headerEnd = frontmatterEnd(lineText);
  const fences = findMarkdownFences(lineText, headerEnd + 1);
  const starts = [0];
  for (const line of lines) starts.push(starts[starts.length - 1] + line.length);
  let plainStart = starts[headerEnd + 1];
  mask.fill(1, 0, plainStart);
  for (const fence of fences) {
    const start = starts[fence.start];
    maskInlineCode(text.slice(plainStart, start), mask, plainStart);
    plainStart = starts[fence.end + 1];
    mask.fill(1, start, plainStart);
  }
  maskInlineCode(text.slice(plainStart), mask, plainStart);
  return mask;
}

const CARD_MARKER = /(#(?:long-)?card(?:-reverse)?)(?=$|\s)(?:[ \t]+id:(\S+))?/gi;

export function cardMarkerAt(line: string, column: number): RegExpMatchArray | undefined {
  return [...line.matchAll(CARD_MARKER)].find((match) => match.index === column);
}

export function cardMarkers(line: string): RegExpMatchArray[] {
  const mask = new Uint8Array(line.length);
  maskInlineCode(line, mask, 0);
  return [...line.matchAll(CARD_MARKER)].filter((match) => {
    const start = match.index!;
    return !mask[start] && !isEscaped(line, start) && !/[\p{L}\p{N}_]$/u.test(line.slice(0, start));
  });
}

export interface ImageDestination {
  start: number;
  end: number;
  target: string;
}

export function markdownImageDestinations(text: string, mask: Uint8Array): ImageDestination[] {
  const results: ImageDestination[] = [];
  for (const image of text.matchAll(/!\[(?:\\.|[^\[\]\\\r\n]|\[[^\]\n]*\])*\]\([ \t]*/g)) {
    const imageStart = image.index!;
    if (mask[imageStart] || isEscaped(text, imageStart)) continue;
    const start = imageStart + image[0].length;
    const remainder = text.slice(start);
    const angle = /^<((?:\\.|[^<>\r\n\\])+)>/.exec(remainder);
    let end = start;
    if (angle) {
      end += angle[0].length;
    } else {
      let depth = 0;
      for (; end < text.length; end += 1) {
        const char = text[end];
        if (/\s/.test(char) || (char === ")" && depth === 0)) break;
        if (char === "\\" && end + 1 < text.length && /[()\\]/.test(text[end + 1])) end += 1;
        else if (char === "(") depth += 1;
        else if (char === ")") depth -= 1;
      }
      if (depth) continue;
    }
    if (end === start) continue;
    const suffix = /^(?:[ \t]+(?:"(?:\\.|[^"\r\n\\])*"|'(?:\\.|[^'\r\n\\])*'|\((?:\\.|[^()\r\n\\])*\)))?[ \t]*\)/.exec(text.slice(end));
    if (!suffix) continue;
    const linkEnd = end + suffix[0].length;
    const target = (angle?.[1] ?? text.slice(start, end)).replace(/\\([!-/:-@\[-`{-~])/g, "$1");
    mask.fill(1, imageStart, linkEnd);
    results.push({ start, end, target });
  }
  return results;
}
