import { App, TFile, normalizePath } from "obsidian";

import { sha256Hex } from "./hash";
import type {
  NoteAttachment,
  NoteSyncFile,
  NoteSyncSource,
  StudySetLinkSource,
  StudySetSource,
} from "./types";

const WIKI_EMBED_RE = /!\[\[([^\]]+)\]\]/g;
const MARKDOWN_IMAGE_RE = /!\[[^\]]*]\(([^)]+)\)/g;
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
  const noteFiles = collectSyncNoteFiles(app, file);
  const attachmentFiles = new Map<string, TFile>();
  const files: NoteSyncFile[] = [];

  for (const noteFile of noteFiles) {
    files.push(await collectNoteFile(app, noteFile, attachmentFiles));
  }

  const attachments = await readAttachments(app, attachmentFiles);
  const primary = files[0];

  return {
    file,
    path: primary.path,
    content: primary.content,
    sourceHash: primary.sourceHash,
    files,
    attachments,
  };
}

export async function collectCurrentStudySetSource(app: App): Promise<StudySetSource> {
  const file = getActiveMarkdownFile(app);
  const content = await app.vault.cachedRead(file);
  const sourceHash = await sha256Hex(content);
  const links = collectLinkedMarkdownNotes(app, file);
  if (links.length === 0) {
    throw new Error("The current note does not contain any markdown note links for a Corvus study preset.");
  }

  return {
    file,
    name: file.basename,
    path: file.path,
    sourceHash,
    links,
  };
}

function getActiveMarkdownFile(app: App): TFile {
  const file = app.workspace.getActiveFile();
  if (!(file instanceof TFile) || file.extension !== "md") {
    throw new Error("Open a markdown note before syncing to Corvus.");
  }
  return file;
}

function collectLinkedMarkdownNotes(app: App, noteFile: TFile): StudySetLinkSource[] {
  const metadata = app.metadataCache.getFileCache(noteFile);
  const links = metadata?.links ?? [];
  const linkedNotes = new Map<string, StudySetLinkSource>();

  for (const link of links) {
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
    if (!linkedNotes.has(linkedFile.path)) {
      linkedNotes.set(linkedFile.path, {
        linkText,
        obsidianPath: linkedFile.path,
      });
    }
  }

  return Array.from(linkedNotes.values());
}

function collectSyncNoteFiles(app: App, noteFile: TFile): TFile[] {
  const files = new Map<string, TFile>();
  files.set(noteFile.path, noteFile);

  for (const linkText of extractQuestionsSourceLinks(app, noteFile)) {
    const linkedFile = app.metadataCache.getFirstLinkpathDest(linkText, noteFile.path);
    if (!(linkedFile instanceof TFile) || linkedFile.extension !== "md") {
      throw new Error(`Question source note not found: ${linkText}`);
    }
    files.set(linkedFile.path, linkedFile);
  }

  return Array.from(files.values());
}

function extractQuestionsSourceLinks(app: App, noteFile: TFile): string[] {
  const metadata = app.metadataCache.getFileCache(noteFile);
  const rawValue = metadata?.frontmatter?.["questions-source"];
  if (rawValue === undefined || rawValue === null) {
    return [];
  }

  const values = Array.isArray(rawValue) ? rawValue : [rawValue];
  const links: string[] = [];
  for (const value of values) {
    const text = String(value);
    const matches = Array.from(text.matchAll(/\[\[([^\]]+)\]\]/g));
    if (matches.length === 0 && text.trim()) {
      throw new Error("questions-source must contain Obsidian wiki link(s).");
    }
    for (const match of matches) {
      const rawLink = match[1] ?? "";
      if (rawLink.includes("#") || rawLink.includes("^")) {
        throw new Error("questions-source links must point to markdown notes, not blocks.");
      }
      const linkText = stripWikiTarget(rawLink);
      if (!linkText) {
        continue;
      }
      links.push(linkText);
    }
  }
  return links;
}

async function collectNoteFile(
  app: App,
  noteFile: TFile,
  attachmentFiles: Map<string, TFile>,
): Promise<NoteSyncFile> {
  const originalContent = await app.vault.cachedRead(noteFile);
  const content = collectAttachmentsAndRewriteContent(app, noteFile, originalContent, attachmentFiles);
  return {
    file: noteFile,
    path: noteFile.path,
    content,
    sourceHash: await sha256Hex(originalContent),
  };
}

function collectAttachmentsAndRewriteContent(
  app: App,
  noteFile: TFile,
  content: string,
  attachmentFiles: Map<string, TFile>,
): string {
  const rewrites: TextRewrite[] = [];

  for (const match of content.matchAll(WIKI_EMBED_RE)) {
    const rawTarget = match[1] ?? "";
    const cleanTarget = stripWikiTarget(rawTarget);
    const attachment = resolveWikiAttachment(app, noteFile, cleanTarget);
    if (attachment) {
      attachmentFiles.set(attachment.path, attachment);
      const targetStart = (match.index ?? 0) + 3;
      rewrites.push({
        start: targetStart,
        end: targetStart + rawTarget.length,
        replacement: rewriteWikiTarget(rawTarget, attachment.path),
      });
    }
  }

  for (const match of content.matchAll(MARKDOWN_IMAGE_RE)) {
    const originalTarget = match[1] ?? "";
    const rawTarget = stripMarkdownTarget(originalTarget);
    const attachment = resolveRelativeAttachment(app, noteFile, rawTarget);
    if (attachment) {
      attachmentFiles.set(attachment.path, attachment);
      const targetStart = (match.index ?? 0) + match[0].lastIndexOf("(") + 1;
      rewrites.push({
        start: targetStart,
        end: targetStart + originalTarget.length,
        replacement: attachment.path,
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
  const value = rawTarget.trim().replace(/^<|>$/g, "");
  if (/^(?:https?:|data:)/i.test(value)) {
    return "";
  }
  return value;
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
  const resolvedPath = noteDirectory
    ? normalizePath(`${noteDirectory}/${relativePath}`)
    : normalizePath(relativePath);
  const file = app.vault.getAbstractFileByPath(resolvedPath);
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

function applyTextRewrites(content: string, rewrites: TextRewrite[]): string {
  if (rewrites.length === 0) {
    return content;
  }

  let rewritten = content;
  for (const rewrite of [...rewrites].sort((a, b) => b.start - a.start)) {
    rewritten = `${rewritten.slice(0, rewrite.start)}${rewrite.replacement}${rewritten.slice(rewrite.end)}`;
  }
  return rewritten;
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
