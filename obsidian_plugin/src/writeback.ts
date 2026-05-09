import { App, TFile } from "obsidian";

import { sha256Hex } from "./hash";
import type { ApplyResponse, PreviewSession } from "./types";

interface WritebackGroup {
  line: number;
  cardId: string | null;
  reverseId: string | null;
}

export async function writeBackImportIds(
  app: App,
  file: TFile,
  preview: PreviewSession,
  result: ApplyResponse,
): Promise<number> {
  const currentContent = await app.vault.cachedRead(file);
  const currentHash = await sha256Hex(currentContent);
  if (currentHash !== preview.source_hash) {
    throw new Error("The note changed after preview. Run the sync again before writing IDs.");
  }

  const previewByIndex = new Map(preview.cards.map((card) => [card.index, card]));
  const grouped = new Map<number, WritebackGroup>();

  for (const applied of result.cards) {
    const previewCard = previewByIndex.get(applied.index);
    if (!previewCard || !previewCard.will_write_back_id || !applied.import_id) {
      continue;
    }
    if (typeof applied.marker_line !== "number") {
      continue;
    }
    const group = grouped.get(applied.marker_line) ?? {
      line: applied.marker_line,
      cardId: null,
      reverseId: null,
    };
    if (applied.marker_kind === "reverse") {
      group.reverseId = applied.import_id;
    } else {
      group.cardId = applied.import_id;
    }
    grouped.set(applied.marker_line, group);
  }

  if (grouped.size === 0) {
    return 0;
  }

  const lines = currentContent.split(/\r?\n/);
  const groups = Array.from(grouped.values()).sort((a, b) => b.line - a.line);
  let writes = 0;

  for (const group of groups) {
    const index = group.line - 1;
    if (index < 0 || index >= lines.length) {
      throw new Error(`Cannot write back ID for marker line ${group.line}.`);
    }
    const updated = replaceMarkerLine(lines[index], group);
    if (updated !== lines[index]) {
      lines[index] = updated;
      writes += 1;
    }
  }

  if (writes > 0) {
    await app.vault.modify(file, lines.join("\n"));
  }

  return writes;
}

function replaceMarkerLine(line: string, group: WritebackGroup): string {
  const markerPattern = /(#card(?:[-/]reverse)?)(?:\s+id:[^\s]+)?/i;
  const match = line.match(markerPattern);
  if (!match) {
    throw new Error(`Marker line ${group.line} no longer contains a supported card marker.`);
  }

  const marker = match[1];
  const isReverseMarker = /#card(?:[-/]reverse)/i.test(marker);

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
