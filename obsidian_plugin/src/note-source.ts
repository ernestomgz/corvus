import { App, TFile, normalizePath } from "obsidian";

import { sha256Hex } from "./hash";
import type { NoteAttachment, NoteSyncSource, StudySetLinkSource, StudySetSource } from "./types";

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
  const content = await app.vault.cachedRead(file);
  const sourceHash = await sha256Hex(content);
  const attachments = await collectAttachments(app, file, content);

  return {
    file,
    path: file.path,
    content,
    sourceHash,
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

async function collectAttachments(app: App, noteFile: TFile, content: string): Promise<NoteAttachment[]> {
  const attachmentFiles = new Map<string, TFile>();

  for (const match of content.matchAll(WIKI_EMBED_RE)) {
    const rawTarget = match[1] ?? "";
    const cleanTarget = stripWikiTarget(rawTarget);
    const attachment = resolveWikiAttachment(app, noteFile, cleanTarget);
    if (attachment) {
      attachmentFiles.set(attachment.path, attachment);
    }
  }

  for (const match of content.matchAll(MARKDOWN_IMAGE_RE)) {
    const rawTarget = stripMarkdownTarget(match[1] ?? "");
    const attachment = resolveRelativeAttachment(app, noteFile, rawTarget);
    if (attachment) {
      attachmentFiles.set(attachment.path, attachment);
    }
  }

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
