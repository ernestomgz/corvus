import { App, TFile, normalizePath, parseYaml } from "obsidian";

import { sha256Hex } from "./hash";
import { isEscaped, markdownCodeMask, markdownImageDestinations } from "./markdown-syntax";
import { prepareTikzAttachments } from "./tikz-attachments";
import { renderTikzImage } from "./tikz-renderer";
import type {
  NoteAttachment,
  NoteSyncSource,
  StudySetLinkSource,
  StudySetSource,
} from "./types";

const WIKI_EMBED_RE = /!\[\[([^\]]+)\]\]/g;
const LOCAL_MEDIA_EXTENSIONS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "svg",
  "webp",
  "bmp",
  "avif",
]);

export async function collectCurrentNoteSource(app: App): Promise<NoteSyncSource> {
  const file = getActiveMarkdownFile(app);
  const originalContent = await app.vault.cachedRead(file);
  return await collectNoteSource(app, file, originalContent);
}

export async function collectNoteSource(app: App, file: TFile, originalContent: string): Promise<NoteSyncSource> {
  const tikz = await prepareTikzAttachments(originalContent, file.path, (markdown, digest) =>
    renderTikzImage(app, markdown, file.path, digest),
  );
  const attachmentFiles = new Map<string, TFile>();
  const rewritten = collectAttachmentsAndRewriteContent(app, file, tikz.content, attachmentFiles, tikz.images);
  const attachments = await readAttachments(app, attachmentFiles);
  for (const [path, data] of tikz.images) {
    if (attachmentFiles.has(path)) throw new Error(`Attachment path conflicts with a rendered TikZ image: ${path}`);
    attachments.push({
      field: `attachment_${attachments.length}`,
      path,
      fileName: path.slice(path.lastIndexOf("/") + 1),
      contentType: "image/png",
      data,
    });
  }

  return {
    file,
    path: file.path,
    content: rewritten.content,
    sourceHash: await sha256Hex(originalContent),
    originalLineNumbers: tikz.originalLineNumbers,
    originalMarkerColumn: rewritten.originalMarkerColumn,
    attachments,
  };
}

export async function collectCurrentStudySetSource(app: App): Promise<StudySetSource> {
  const file = getActiveMarkdownFile(app);
  const content = await app.vault.cachedRead(file);
  const sourceHash = await sha256Hex(content);
  const { links, errors } = await collectLinkedMarkdownNotes(app, file);
  if (links.length === 0) {
    throw new Error(errors[0] ?? "The current note does not contain any markdown note links for a Corvus study preset.");
  }

  return {
    file,
    name: file.basename,
    path: file.path,
    sourceHash,
    links,
    errors,
  };
}

function getActiveMarkdownFile(app: App): TFile {
  const file = app.workspace.getActiveFile();
  if (!(file instanceof TFile) || file.extension !== "md") {
    throw new Error("Open a markdown note before syncing to Corvus.");
  }
  return file;
}

const COMPANION_TYPES = { "questions-source": "questions", "exercises-source": "exercises" } as const;

async function collectLinkedMarkdownNotes(app: App, noteFile: TFile): Promise<{ links: StudySetLinkSource[]; errors: string[] }> {
  const metadata = app.metadataCache.getFileCache(noteFile);
  const directLinks = metadata?.links ?? [];
  const linkedNotes = new Map<string, StudySetLinkSource>();
  const frontmatterByPath = new Map<string, Record<string, unknown>>();
  const errors = new Set<string>();

  async function readFrontmatter(file: TFile): Promise<Record<string, unknown>> {
    const cached = frontmatterByPath.get(file.path);
    if (cached) return cached;
    const content = await app.vault.cachedRead(file);
    const lines = content.split(/\r?\n/);
    const end = lines[0]?.trim() === "---"
      ? lines.findIndex((line, index) => index > 0 && ["---", "..."].includes(line.trim()))
      : -1;
    let fields = {};
    if (end > 0) {
      try {
        // Older examples used quoted wiki links without a space after the colon.
        const yaml = lines.slice(1, end).join("\n").replace(/^(questions-source|exercises-source):(?=["'\[])/gm, "$1: ");
        const parsed = parseYaml(yaml);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) fields = parsed;
      } catch {
        errors.add(`${file.path}: Invalid YAML frontmatter.`);
      }
    }
    frontmatterByPath.set(file.path, fields);
    return fields;
  }

  async function addSource(file: TFile, linkText: string, inferredType?: string): Promise<void> {
    const explicit = (await readFrontmatter(file))["note-type"];
    let noteType = inferredType;
    const noteTypeExplicit = explicit != null && explicit !== "";
    if (noteTypeExplicit) {
      const slug = typeof explicit === "string" ? explicit.trim().toLowerCase().replace(/\s+/g, "-") : "";
      if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug) || slug.length > 64) {
        errors.add(`${file.path} → note-type: Use letters, numbers, spaces, or hyphens.`);
      } else {
        noteType = slug;
      }
    }
    const existing = linkedNotes.get(file.path);
    if (!existing || (noteType && !existing.noteType)) {
      linkedNotes.set(file.path, { linkText, obsidianPath: file.path, noteType, noteTypeExplicit });
    } else if (noteType && existing.noteType !== noteType && !noteTypeExplicit) {
      errors.add(`${file.path} is linked as both questions and exercises. Set note-type on that note to choose its type.`);
    }
  }

  const expanded = new Set<string>();
  for (const link of directLinks) {
    const linkText = link.link.trim();
    const original = (link.original ?? "").trim();
    if (!linkText || !original.startsWith("[[")) {
      continue;
    }
    if (linkText.includes("#") || linkText.includes("^")) {
      continue;
    }

    const linkedFile = app.metadataCache.getFirstLinkpathDest(linkText, noteFile.path);
    if (!(linkedFile instanceof TFile) || linkedFile.extension !== "md") {
      continue;
    }
    await addSource(linkedFile, linkText);
    if (expanded.has(linkedFile.path)) continue;
    expanded.add(linkedFile.path);
    const fields = await readFrontmatter(linkedFile);
    for (const [field, noteType] of Object.entries(COMPANION_TYPES)) {
      const context = `${linkedFile.path} → ${field}`;
      for (const target of readCompanionLinks(fields[field], context, errors)) {
        const companion = app.metadataCache.getFirstLinkpathDest(target, linkedFile.path);
        if (!(companion instanceof TFile) || companion.extension !== "md") {
          errors.add(`${context}: Link not found: [[${target}]].`);
          continue;
        }
        await addSource(companion, target, noteType);
      }
    }
  }

  return { links: [...linkedNotes.values()], errors: [...errors] };
}

function readCompanionLinks(value: unknown, context: string, errors: Set<string>): string[] {
  if (value == null) return [];
  const links: string[] = [];
  for (const entry of Array.isArray(value) ? value : [value]) {
    if (typeof entry !== "string") {
      errors.add(`${context}: Use Obsidian wiki links.`);
      continue;
    }
    const matches = [...entry.matchAll(/\[\[([^\]]+)\]\]/g)];
    if (!matches.length && entry.trim()) errors.add(`${context}: Use Obsidian wiki links.`);
    for (const [, raw] of matches) {
      const target = raw.split("|", 1)[0].trim();
      if (!target || /[#^]/.test(target)) {
        errors.add(`${context}: [[${raw}]] must point to a whole Markdown note.`);
      } else {
        links.push(target);
      }
    }
  }
  return links;
}

function collectAttachmentsAndRewriteContent(
  app: App,
  noteFile: TFile,
  content: string,
  attachmentFiles: Map<string, TFile>,
  generatedImages: Map<string, ArrayBuffer>,
): { content: string; originalMarkerColumn?: NoteSyncSource["originalMarkerColumn"] } {
  const rewrites: TextRewrite[] = [];
  const mask = markdownCodeMask(content);
  for (const image of markdownImageDestinations(content, mask)) {
    const target = stripMarkdownTarget(image.target);
    if (generatedImages.has(target)) continue;
    const attachment = resolveRelativeAttachment(app, noteFile, target);
    if (attachment) {
      attachmentFiles.set(attachment.path, attachment);
      rewrites.push({ start: image.start, end: image.end, replacement: `</${attachment.path.split("/").map(encodeURIComponent).join("/")}>` });
    }
  }

  for (const match of content.matchAll(WIKI_EMBED_RE)) {
    const start = match.index!;
    if (mask[start] || isEscaped(content, start)) continue;
    const rawTarget = match[1] ?? "";
    const cleanTarget = stripWikiTarget(rawTarget);
    const attachment = resolveWikiAttachment(app, noteFile, cleanTarget);
    if (attachment) {
      attachmentFiles.set(attachment.path, attachment);
      const targetStart = start + 3;
      rewrites.push({
        start: targetStart,
        end: targetStart + rawTarget.length,
        replacement: rewriteWikiTarget(rawTarget, `/${attachment.path}`),
      });
    }
  }

  return applyTextRewrites(content, rewrites);
}

async function readAttachments(app: App, attachmentFiles: Map<string, TFile>): Promise<NoteAttachment[]> {
  const attachments: NoteAttachment[] = [];
  let index = 0;
  for (const file of attachmentFiles.values()) {
    const data = await app.vault.readBinary(file);
    attachments.push({
      field: `attachment_${index}`,
      path: file.path,
      fileName: file.name,
      contentType: detectContentType(file.extension),
      data,
    });
    index += 1;
  }

  return attachments;
}

function stripWikiTarget(rawTarget: string): string {
  const [beforePipe] = rawTarget.split("|", 1);
  const [beforeAnchor] = beforePipe.split("#", 1);
  return beforeAnchor.trim();
}

function rewriteWikiTarget(rawTarget: string, resolvedPath: string): string {
  const pipeIndex = rawTarget.indexOf("|");
  if (pipeIndex === -1) {
    return resolvedPath;
  }
  return `${resolvedPath}${rawTarget.slice(pipeIndex)}`;
}

function stripMarkdownTarget(rawTarget: string): string {
  if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(rawTarget)) return "";
  try {
    return decodeURIComponent(rawTarget);
  } catch {
    return rawTarget;
  }
}

function resolveWikiAttachment(app: App, noteFile: TFile, linkPath: string): TFile | null {
  if (!linkPath) {
    return null;
  }
  const file = app.metadataCache.getFirstLinkpathDest(linkPath, noteFile.path);
  if (!(file instanceof TFile)) {
    return null;
  }
  return isLocalMediaFile(file) ? file : null;
}

function resolveRelativeAttachment(app: App, noteFile: TFile, relativePath: string): TFile | null {
  if (!relativePath) {
    return null;
  }
  const noteDirectory = noteFile.parent?.path ?? "";
  const target = relativePath.startsWith("/") || !noteDirectory ? relativePath : `${noteDirectory}/${relativePath}`;
  const segments: string[] = [];
  for (const segment of normalizePath(target).split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") {
      if (!segments.length) return null;
      segments.pop();
    } else {
      segments.push(segment);
    }
  }
  const file = app.vault.getAbstractFileByPath(segments.join("/"));
  if (!(file instanceof TFile)) {
    return null;
  }
  return isLocalMediaFile(file) ? file : null;
}

function isLocalMediaFile(file: TFile): boolean {
  return LOCAL_MEDIA_EXTENSIONS.has(file.extension.toLowerCase());
}

interface TextRewrite {
  start: number;
  end: number;
  replacement: string;
}

function applyTextRewrites(content: string, rewrites: TextRewrite[]): {
  content: string;
  originalMarkerColumn?: NoteSyncSource["originalMarkerColumn"];
} {
  if (!rewrites.length) return { content };
  const editsByLine = new Map<number, { end: number; delta: number }[]>();
  const parts: string[] = [];
  let cursor = 0;
  let line = 1;
  let lineStart = 0;
  let delta = 0;
  let nextBreak = content.indexOf("\n");
  for (const edit of rewrites.sort((a, b) => a.start - b.start)) {
    while (nextBreak !== -1 && nextBreak < edit.start) {
      line += 1;
      lineStart = nextBreak + 1;
      nextBreak = content.indexOf("\n", lineStart);
      delta = 0;
    }
    const outputEnd = edit.start - lineStart + delta + edit.replacement.length;
    const difference = edit.replacement.length - (edit.end - edit.start);
    const edits = editsByLine.get(line) ?? [];
    edits.push({ end: outputEnd, delta: difference });
    editsByLine.set(line, edits);
    delta += difference;
    parts.push(content.slice(cursor, edit.start), edit.replacement);
    cursor = edit.end;
  }
  parts.push(content.slice(cursor));
  return {
    content: parts.join(""),
    originalMarkerColumn: (lineNumber, column) => column - (editsByLine.get(lineNumber) ?? [])
      .filter(({ end }) => end <= column).reduce((total, edit) => total + edit.delta, 0),
  };
}

function detectContentType(extension: string): string {
  switch (extension.toLowerCase()) {
    case "jpg":
    case "jpeg":
      return "image/jpeg";
    case "png":
      return "image/png";
    case "gif":
      return "image/gif";
    case "svg":
      return "image/svg+xml";
    case "webp":
      return "image/webp";
    case "bmp":
      return "image/bmp";
    case "avif":
      return "image/avif";
    default:
      return "application/octet-stream";
  }
}
