import { Modal, Notice, type App, type TFile } from "obsidian";

import type { NoteSyncAudit } from "./types";
import { findVaultIdConflicts } from "./vault-sync-conflicts";

export type VaultSyncRow = { file: TFile } & ({ audit: NoteSyncAudit } | { error: string });

function hasRepeatedMarkerId(audit: NoteSyncAudit): boolean {
  const markers = audit.identities.filter(({ kind }) => kind === "marker").map(({ value }) => value);
  return new Set(markers).size < markers.length;
}

export class VaultSyncModal extends Modal {
  constructor(app: App, private readonly rows: VaultSyncRow[]) {
    super(app);
    this.modalEl.addClass("corvus-vault-sync-modal");
  }

  onOpen(): void {
    const { contentEl } = this;
    this.setTitle("Vault Sync Check");
    if (this.rows.length === 0) {
      contentEl.createEl("p", { text: "No Corvus cards found in this vault." });
      return;
    }

    const conflicts = findVaultIdConflicts(this.rows.flatMap((row) =>
      "audit" in row ? [{ path: row.file.path, audit: row.audit }] : [],
    ));
    const sorted = this.rows.map((row) => {
      const audit = "audit" in row ? row.audit : null;
      const warnings = conflicts.get(row.file.path) ?? [];
      const idIssues = warnings.length > 0 || (audit !== null && (
        audit.found < audit.identified || audit.without_ids > 0 || hasRepeatedMarkerId(audit)
      ));
      const updates = (audit?.updates ?? 0) > 0;
      const moves = (audit?.moves ?? 0) > 0;
      const upToDate = audit !== null && !updates && !moves && !idIssues && audit.errors.length === 0;
      return { row, warnings, idIssues, updates, moves, upToDate };
    }).sort((a, b) =>
      Number("error" in b.row) - Number("error" in a.row) ||
      Number(a.upToDate) - Number(b.upToDate) ||
      a.row.file.path.localeCompare(b.row.file.path),
    );
    const filters = [
      { label: "All", rows: sorted, empty: "No notes found." },
      { label: "Updates", rows: sorted.filter(({ updates }) => updates), empty: "No updates found." },
      { label: "Deck moves", rows: sorted.filter(({ moves }) => moves), empty: "No deck moves found." },
      { label: "ID issues", rows: sorted.filter(({ idIssues }) => idIssues), empty: "No ID issues found." },
      { label: "Up to date", rows: sorted.filter(({ upToDate }) => upToDate), empty: "No notes are up to date." },
    ];
    const toolbar = contentEl.createDiv({
      cls: "corvus-vault-sync-filters",
      attr: { role: "group", "aria-label": "Filter notes" },
    });
    const results = contentEl.createDiv();
    const buttons = filters.map(({ label, rows }) => toolbar.createEl("button", {
      text: `${label} (${rows.length})`,
      attr: { type: "button", "aria-pressed": "false" },
    }));

    const selectFilter = (index: number) => {
      buttons.forEach((button, i) => button.setAttribute("aria-pressed", String(i === index)));
      results.empty();
      const { empty, rows } = filters[index];
      if (rows.length === 0) {
        results.createEl("p", { cls: "corvus-vault-sync-empty", text: empty });
        return;
      }

      const header = results.createDiv({ cls: "corvus-vault-sync-header", attr: { "aria-hidden": "true" } });
      for (const label of ["Note", "IDs found", "Status"]) header.createSpan({ text: label });
      for (const { row, warnings } of rows) this.renderRow(results, row, warnings);
    };

    buttons.forEach((button, index) => button.addEventListener("click", () => selectFilter(index)));
    selectFilter(0);
  }

  private renderRow(parent: HTMLElement, row: VaultSyncRow, conflicts: string[]): void {
    const button = parent.createEl("button", { cls: "corvus-vault-sync-note", attr: { type: "button" } });
    const note = button.createSpan({ cls: "corvus-vault-sync-path" });
    note.createEl("strong", { text: row.file.name });
    const folder = row.file.path.slice(0, Math.max(0, row.file.path.lastIndexOf("/")));
    if (folder) note.createSpan({ cls: "corvus-vault-sync-folder", text: folder });

    const audit = "audit" in row ? row.audit : null;
    const count = button.createSpan({ cls: "corvus-vault-sync-count" });
    if (audit) {
      count.textContent = `${audit.found} / ${audit.identified}`;
      count.setAttribute("aria-label", `${audit.found} of ${audit.identified} card IDs found`);
    } else {
      count.textContent = "—";
    }

    const details = "error" in row ? [row.error] : [...conflicts, ...row.audit.errors];
    const status: string[] = [];
    if (audit) {
      const missing = audit.identified - audit.found;
      if (missing) status.push(`${missing} missing`);
      if (audit.without_ids) status.push(`${audit.without_ids} without IDs`);
      if (audit.updates) status.push(`${audit.updates} to update${audit.moves ? ` (${audit.moves} to move)` : ""}`);
      if (status.length && audit.unchanged) status.push(`${audit.unchanged} up to date`);
    }
    const statusText = status.join(" · ") || (details.length ? "Needs review" : "Up to date");
    button.createSpan({ cls: "corvus-vault-sync-status", text: statusText });
    if (details.length) {
      const warnings = button.createSpan({ cls: "corvus-vault-sync-warnings" });
      for (const detail of details) warnings.createSpan({ text: detail });
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

  onClose(): void {
    this.contentEl.empty();
  }
}
