import { App, TFile } from "obsidian";

import { sha256Hex } from "./hash";
import { cardMarkerAt, cardMarkers } from "./markdown-syntax";
import type { ApplyResponse, PreviewSession } from "./types";

interface WritebackGroup {
  line: number;
  column: number | null;
  cardId: string | null;
  reverseId: string | null;
}

export async function writeBackImportIds(
  app: App,
  file: TFile,
  preview: PreviewSession,
  result: ApplyResponse,
): Promise<number> {
  const previewByIndex = new Map(preview.cards.map((card) => [card.index, card]));
  const grouped = new Map<number, WritebackGroup>();

  for (const applied of result.cards) {
    const previewCard = previewByIndex.get(applied.index);
    if (!previewCard || !previewCard.will_write_back_id || !applied.import_id) {
      continue;
    }
    // Preview lines were translated back to the vault note before it was displayed.
    if (typeof previewCard.marker_line !== "number") {
      continue;
    }
    const group = grouped.get(previewCard.marker_line) ?? {
      line: previewCard.marker_line,
      column: previewCard.marker_column ?? null,
      cardId: null,
      reverseId: null,
    };
    if (applied.marker_kind === "reverse") {
      group.reverseId = applied.import_id;
    } else {
      group.cardId = applied.import_id;
    }
    grouped.set(group.line, group);
  }

  if (grouped.size === 0) {
    return 0;
  }

  const currentContent = await app.vault.cachedRead(file);
  const changedMessage = "Corvus saved the cards, but the note changed before their IDs could be written.";
  if (await sha256Hex(currentContent) !== preview.source_hash) {
    throw new Error(changedMessage);
  }

  const lines = currentContent.split(/(?<=\n)/);
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
    const updated = lines.join("");
    await app.vault.process(file, (latest) => {
      if (latest !== currentContent) {
        throw new Error(changedMessage);
      }
      return updated;
    });
  }

  return writes;
}

function replaceMarkerLine(line: string, group: WritebackGroup): string {
  const candidates = group.column === null ? cardMarkers(line) : [];
  const match = group.column === null
    ? (candidates.length === 1 ? candidates[0] : undefined)
    : cardMarkerAt(line, group.column);
  if (!match) {
    throw new Error(`Could not locate the card marker on line ${group.line}.`);
  }

  const marker = match[1];
  const [currentId = "", currentReverseId = ""] = (match[2] ?? "").split("|", 2);
  const forwardId = group.cardId ?? currentId;
  const reverseId = group.reverseId ?? currentReverseId;
  const ids = marker.toLowerCase().endsWith("-reverse") && reverseId ? `${forwardId}|${reverseId}` : forwardId;
  if (!ids) return line;
  const start = match.index!;
  return `${line.slice(0, start)}${marker} id:${ids}${line.slice(start + match[0].length)}`;
}
