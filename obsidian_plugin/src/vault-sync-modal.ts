import { Modal, Notice, type App, type TFile } from "obsidian";

import type { NoteSyncAudit } from "./types";
import { findVaultIdConflicts } from "./vault-sync-conflicts";

export type VaultSyncRow = { file: TFile } & ({ audit: NoteSyncAudit } | { error: string });

function needsAttention(row: VaultSyncRow): boolean {
  if ("error" in row) return true;
  const { audit } = row;
  return audit.found < audit.identified || audit.updates > 0 || audit.without_ids > 0 || audit.errors.length > 0;
}

export class VaultSyncModal extends Modal {
  constructor(app: App, private readonly rows: VaultSyncRow[]) {
    super(app);
    this.modalEl.addClass("corvus-preview-modal");
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.createEl("h2", { text: "Corvus Vault Sync Check" });
    if (this.rows.length === 0) {
      contentEl.createEl("p", { text: "No Corvus cards found in this vault." });
      return;
    }

    const conflicts = findVaultIdConflicts(this.rows.flatMap((row) =>
      "audit" in row ? [{ path: row.file.path, audit: row.audit }] : [],
    ));
    const attentionRequired = (row: VaultSyncRow) => conflicts.has(row.file.path) || needsAttention(row);
    const attention = this.rows.filter(attentionRequired).length;
    contentEl.createEl("p", { text: `${attention} of ${this.rows.length} notes need attention` });
    const list = contentEl.createDiv({ cls: "corvus-vault-sync-list" });
    const sorted = [...this.rows].sort((a, b) =>
      Number(attentionRequired(b)) - Number(attentionRequired(a)) || a.file.path.localeCompare(b.file.path),
    );
    for (const row of sorted) {
      const button = list.createEl("button", { cls: "corvus-vault-sync-note", attr: { type: "button" } });
      button.createEl("strong", { text: row.file.path });
      for (const conflict of conflicts.get(row.file.path) ?? []) button.createSpan({ text: conflict });
      if ("error" in row) {
        button.createSpan({ text: row.error });
      } else {
        const { audit } = row;
        const summary = [`${audit.found} of ${audit.identified} cards with IDs found in Corvus`];
        if (audit.identified > audit.found) summary.push(`${audit.identified - audit.found} IDs missing`);
        if (audit.updates) summary.push(`${audit.updates} need updates${audit.moves ? ` (${audit.moves} deck moves)` : ""}`);
        if (audit.unchanged) summary.push(`${audit.unchanged} up to date`);
        if (audit.without_ids) summary.push(`${audit.without_ids} without IDs`);
        button.createSpan({ text: summary.join(" · ") });
        for (const error of audit.errors) button.createSpan({ text: error });
      }
      button.addEventListener("click", async () => {
        try {
          await this.app.workspace.getLeaf(false).openFile(row.file);
          this.close();
        } catch {
          new Notice(`Could not open ${row.file.path}`);
        }
      });
    }
  }

  onClose(): void {
    this.contentEl.empty();
  }
}
