import { ButtonComponent, Modal, Notice, TFile } from "obsidian";

import type { StudySetPreview } from "./types";

export class StudySetPreviewModal extends Modal {
  private resolved = false;

  private resolver: ((value: boolean) => void) | null = null;

  constructor(app: Modal["app"], private readonly preview: StudySetPreview, private readonly sourceErrors: string[]) {
    super(app);
    this.modalEl.addClass("corvus-preview-modal");
  }

  async openAndWait(): Promise<boolean> {
    return await new Promise<boolean>((resolve) => {
      this.resolver = resolve;
      this.open();
    });
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();

    contentEl.createEl("h2", { text: "Corvus Study Preset Preview" });

    const summaryEl = contentEl.createDiv({ cls: "corvus-preview-summary" });
    summaryEl.createEl("div", { text: `Source: ${this.preview.source_path}` });
    summaryEl.createEl("div", { text: `Preset: ${this.preview.name}` });
    summaryEl.createEl("div", { text: `Root deck: ${this.preview.root_deck_path}` });
    summaryEl.createEl("div", {
      text:
        `${this.preview.will_update ? "Update" : "Create"} preset with ` +
        `${this.preview.summary.source_path_count} source note(s), ${this.preview.summary.card_count} card(s)`,
    });

    const table = contentEl.createEl("table", { cls: "corvus-study-sources" });
    const header = table.createEl("thead").createEl("tr");
    for (const title of ["Note", "Type", "Status"]) header.createEl("th", { text: title, attr: { scope: "col" } });
    const body = table.createEl("tbody");
    for (const source of [...this.preview.source_paths, ...this.preview.missing]) {
      const row = body.createEl("tr");
      const note = row.createEl("td");
      const link = note.createEl("button", {
        cls: "corvus-study-source-link", text: source.obsidian_path, attr: { type: "button" },
      });
      link.addEventListener("click", () => void this.openSource(source.obsidian_path));
      const label = (source.note_type ?? "questions").replace(/-/g, " ");
      row.createEl("td", { text: label.charAt(0).toUpperCase() + label.slice(1) });
      const status = "card_count" in source ? `${source.card_count} ${source.card_count === 1 ? "card" : "cards"}` : "No cards in this root deck";
      row.createEl("td", { text: status });
    }

    if (this.preview.missing.length > 0 && !this.preview.has_errors && !this.sourceErrors.length) {
      contentEl.createEl("p", { text: "Only notes with cards in Corvus will be included." });
    }
    const errors = [...this.sourceErrors, ...this.preview.errors];
    if (errors.length > 0) {
      const errorsEl = contentEl.createDiv({ cls: "corvus-preview-errors" });
      errorsEl.createEl("strong", { text: "Errors" });
      const list = errorsEl.createEl("ul");
      for (const error of errors) {
        list.createEl("li", { text: error });
      }
    }

    const actionsEl = contentEl.createDiv({ cls: "corvus-preview-actions" });
    new ButtonComponent(actionsEl)
      .setButtonText("Cancel")
      .onClick(() => this.finish(false));
    new ButtonComponent(actionsEl)
      .setCta()
      .setButtonText(this.preview.will_update ? "Update Preset" : "Create Preset")
      .setDisabled(this.preview.has_errors || this.sourceErrors.length > 0)
      .onClick(() => this.finish(true));
  }

  private async openSource(path: string): Promise<void> {
    const file = this.app.vault.getAbstractFileByPath(path);
    if (!(file instanceof TFile)) {
      new Notice(`Could not open ${path}.`);
      return;
    }
    try {
      await this.app.workspace.getLeaf(false).openFile(file);
      this.finish(false);
    } catch {
      new Notice(`Could not open ${path}.`);
    }
  }

  onClose(): void {
    this.contentEl.empty();
    if (!this.resolved && this.resolver) {
      this.resolved = true;
      this.resolver(false);
    }
  }

  private finish(value: boolean): void {
    if (!this.resolver || this.resolved) {
      this.close();
      return;
    }
    this.resolved = true;
    this.resolver(value);
    this.close();
  }
}
