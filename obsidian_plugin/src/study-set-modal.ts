import { ButtonComponent, Modal } from "obsidian";

import type { StudySetPreview } from "./types";

export class StudySetPreviewModal extends Modal {
  private resolved = false;

  private resolver: ((value: boolean) => void) | null = null;

  constructor(app: Modal["app"], private readonly preview: StudySetPreview) {
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
      text: `${this.preview.will_update ? "Update" : "Create"} preset with ${this.preview.summary.deck_count} deck(s)`,
    });

    if (this.preview.decks.length > 0) {
      const decksEl = contentEl.createDiv({ cls: "corvus-preview-decks" });
      decksEl.createEl("strong", { text: "Resolved decks" });
      const list = decksEl.createEl("ul");
      for (const deck of this.preview.decks) {
        list.createEl("li", {
          text: `${deck.obsidian_path} -> ${deck.full_path}`,
        });
      }
    }

    if (this.preview.missing.length > 0) {
      const missingEl = contentEl.createDiv({ cls: "corvus-preview-errors" });
      missingEl.createEl("strong", { text: "Missing decks" });
      const list = missingEl.createEl("ul");
      for (const missing of this.preview.missing) {
        list.createEl("li", {
          text: `${missing.obsidian_path} -> ${missing.target_deck_path}`,
        });
      }
    }

    if (this.preview.errors.length > 0) {
      const errorsEl = contentEl.createDiv({ cls: "corvus-preview-errors" });
      errorsEl.createEl("strong", { text: "Errors" });
      const list = errorsEl.createEl("ul");
      for (const error of this.preview.errors) {
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
      .setDisabled(this.preview.has_errors)
      .onClick(() => this.finish(true));
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
