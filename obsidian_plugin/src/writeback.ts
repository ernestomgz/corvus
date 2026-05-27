import { App } from "obsidian";

import { sha256Hex } from "./hash";
import type { ApplyResponse, NoteSyncSource, PreviewSession } from "./types";

interface WritebackGroup {
  line: number;
  cardId: string | null;
  reverseId: string | null;
  sourcePath: string;
}

export async function writeBackImportIds(
  app: App,
  source: NoteSyncSource,
  preview: PreviewSession,
  result: ApplyResponse,
): Promise<number> {
  const previewByIndex = new Map(preview.cards.map((card) => [card.index, card]));
  const sourceFiles = new Map(source.files.map((file) => [file.path, file]));
  const grouped = new Map<string, WritebackGroup>();

  for (const applied of result.cards) {
    const previewCard = previewByIndex.get(applied.index);
    if (!previewCard || !previewCard.will_write_back_id || !applied.import_id) {
      continue;
    }
    if (typeof applied.marker_line !== "number") {
      continue;
    }
    const sourcePath = applied.source_path ?? previewCard.source_path ?? source.path;
    const groupKey = `${sourcePath}:${applied.marker_line}`;
    const group = grouped.get(groupKey) ?? {
      line: applied.marker_line,
      cardId: null,
      reverseId: null,
      sourcePath,
    };
    if (applied.marker_kind === "reverse") {
      group.reverseId = applied.import_id;
    } else {
      group.cardId = applied.import_id;
    }
    grouped.set(groupKey, group);
  }

  if (grouped.size === 0) {
    return 0;
  }

  let writes = 0;
  const groupsBySource = new Map<string, WritebackGroup[]>();
  for (const group of grouped.values()) {
    const groups = groupsBySource.get(group.sourcePath) ?? [];
    groups.push(group);
    groupsBySource.set(group.sourcePath, groups);
  }

  for (const [sourcePath, groups] of groupsBySource.entries()) {
    const sourceFile = sourceFiles.get(sourcePath);
    if (!sourceFile) {
      throw new Error(`Cannot write back IDs for unknown source note ${sourcePath}.`);
    }

    const currentContent = await app.vault.cachedRead(sourceFile.file);
    const currentHash = await sha256Hex(currentContent);
    if (currentHash !== sourceFile.sourceHash) {
      throw new Error(`The note ${sourcePath} changed after preview. Run the sync again before writing IDs.`);
    }

    const lines = currentContent.split(/\r?\n/);
    let fileWrites = 0;
    for (const group of groups.sort((a, b) => b.line - a.line)) {
      const index = group.line - 1;
      if (index < 0 || index >= lines.length) {
        throw new Error(`Cannot write back ID for marker line ${group.line} in ${sourcePath}.`);
      }
      const updated = replaceMarkerLine(lines[index], group);
      if (updated !== lines[index]) {
        lines[index] = updated;
        fileWrites += 1;
      }
    }

    if (fileWrites > 0) {
      await app.vault.modify(sourceFile.file, lines.join("\n"));
      writes += fileWrites;
    }
  }

  return writes;
}

function replaceMarkerLine(line: string, group: WritebackGroup): string {
  const markerPattern = /(#(?:long-)?card(?:-reverse)?)(?=$|\s)(?:\s+id:[^\s]+)?/i;
  const match = line.match(markerPattern);
  if (!match) {
    throw new Error(`Marker line ${group.line} no longer contains a supported card marker.`);
  }

  const marker = match[1];
  const isReverseMarker = /#(?:long-)?card-reverse/i.test(marker);

  if (isReverseMarker) {
    if (group.cardId && group.reverseId) {
      return line.replace(markerPattern, `${marker} id:${group.cardId}|${group.reverseId}`);
    }
    if (group.cardId) {
      return line.replace(markerPattern, `${marker} id:${group.cardId}`);
    }
    if (group.reverseId) {
      return line.replace(markerPattern, `${marker} id:${group.reverseId}`);
    }
    return line;
  }

  if (!group.cardId) {
    return line;
  }

  return line.replace(markerPattern, `${marker} id:${group.cardId}`);
}
