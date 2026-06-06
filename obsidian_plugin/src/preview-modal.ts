import { ButtonComponent, MarkdownRenderChild, MarkdownRenderer, Modal } from "obsidian";

import type { ApplyDecision, PreviewCard, PreviewSession } from "./types";

export class SyncPreviewModal extends Modal {
  private readonly selections = new Map<number, boolean>();

  private resolved = false;

  private resolver: ((value: ApplyDecision[] | null) => void) | null = null;

  private readonly markdownChildren: MarkdownRenderChild[] = [];

  constructor(app: Modal["app"], private readonly preview: PreviewSession) {
    super(app);
    this.modalEl.addClass("corvus-preview-modal");
    for (const card of preview.cards) {
      this.selections.set(card.index, !card.unchanged && card.errors.length === 0);
    }
  }

  async openAndWait(): Promise<ApplyDecision[] | null> {
    return await new Promise<ApplyDecision[] | null>((resolve) => {
      this.resolver = resolve;
      this.open();
    });
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();

    contentEl.createEl("h2", { text: "Corvus Sync Preview" });

    const summaryEl = contentEl.createDiv({ cls: "corvus-preview-summary" });
    summaryEl.createEl("div", { text: `Source: ${this.preview.source_path}` });
    summaryEl.createEl("div", { text: `Root deck: ${this.preview.root_deck_path}` });
    summaryEl.createEl("div", {
      text:
        `Create ${this.preview.summary.creates ?? 0}, ` +
        `update ${this.preview.summary.updates ?? 0}, ` +
        `unchanged ${this.preview.summary.unchanged ?? 0}, ` +
        `media ${this.preview.summary.media_copied ?? 0}`,
    });

    if (this.preview.planned_decks.length > 0) {
      const decksEl = contentEl.createDiv({ cls: "corvus-preview-decks" });
      decksEl.createEl("strong", { text: "Decks to create" });
      const list = decksEl.createEl("ul");
      for (const deck of this.preview.planned_decks) {
        list.createEl("li", { text: deck });
      }
    }

    const cardsContainer = contentEl.createDiv();
    for (const card of this.preview.cards) {
      cardsContainer.appendChild(this.renderCard(card));
    }

    const hasBlockingErrors = this.preview.cards.some((card) => card.errors.length > 0);
    const actionsEl = contentEl.createDiv({ cls: "corvus-preview-actions" });
    new ButtonComponent(actionsEl)
      .setButtonText("Cancel")
      .onClick(() => this.finish(null));
    new ButtonComponent(actionsEl)
      .setCta()
      .setButtonText("Apply Selected")
      .setDisabled(hasBlockingErrors)
      .onClick(() => {
        const decisions: ApplyDecision[] = this.preview.cards.map((card) => ({
          index: card.index,
          action: this.selections.get(card.index) ? "apply" : "skip",
        }));
        this.finish(decisions);
      });
  }

  onClose(): void {
    for (const child of this.markdownChildren) {
      child.unload();
    }
    this.markdownChildren.length = 0;
    this.contentEl.empty();
    if (!this.resolved && this.resolver) {
      this.resolved = true;
      this.resolver(null);
    }
  }

  private finish(value: ApplyDecision[] | null): void {
    if (!this.resolver || this.resolved) {
      this.close();
      return;
    }
    this.resolved = true;
    this.resolver(value);
    this.close();
  }

  private renderCard(card: PreviewCard): HTMLElement {
    const cardEl = document.createElement("div");
    cardEl.addClass("corvus-preview-card");
    if (card.errors.length > 0) {
      cardEl.addClass("is-error");
    } else if (card.existing) {
      cardEl.addClass("is-warning");
    }

    const headerEl = cardEl.createDiv({ cls: "corvus-preview-card-header" });
    const leftEl = headerEl.createDiv();
    leftEl.createEl("div", {
      cls: "corvus-preview-card-title",
      text: card.import_id ? `ID ${card.import_id}` : "New card",
    });
    leftEl.createEl("div", {
      cls: "corvus-preview-card-meta",
      text: `Line ${card.marker_line} | ${card.target_deck_path}`,
    });

    const rightEl = headerEl.createDiv({ cls: "corvus-preview-badges" });
    rightEl.createSpan({
      cls: "corvus-preview-badge",
      text: this.actionLabel(card),
    });
    if (card.will_write_back_id) {
      rightEl.createSpan({
        cls: "corvus-preview-badge",
        text: "Writes ID to note",
      });
    }
    if (card.metadata_changes?.deck) {
      rightEl.createSpan({
        cls: "corvus-preview-badge",
        text: "Deck changes",
      });
    }

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = this.selections.get(card.index) ?? false;
    checkbox.disabled = card.errors.length > 0;
    checkbox.addEventListener("change", () => {
      this.selections.set(card.index, checkbox.checked);
    });
    headerEl.prepend(checkbox);

    this.renderMetadataChanges(cardEl, card);
    this.renderCardFace(cardEl, "Front", card.front_md || "(empty front)", "corvus-preview-card-front");
    this.renderCardFace(cardEl, "Back", card.back_md || "(empty back)", "corvus-preview-card-back");

    if (card.warnings.length > 0) {
      const warningsEl = cardEl.createDiv({ cls: "corvus-preview-errors" });
      warningsEl.createEl("strong", { text: "Warnings" });
      const list = warningsEl.createEl("ul");
      for (const warning of card.warnings) {
        list.createEl("li", { text: warning });
      }
    }

    if (card.errors.length > 0) {
      const errorsEl = cardEl.createDiv({ cls: "corvus-preview-errors" });
      errorsEl.createEl("strong", { text: "Errors" });
      const list = errorsEl.createEl("ul");
      for (const error of card.errors) {
        list.createEl("li", { text: error });
      }
    }

    return cardEl;
  }

  private renderMetadataChanges(parent: HTMLElement, card: PreviewCard): void {
    const changes = card.metadata_changes ?? {};
    const rows: string[] = [];

    if (changes.deck) {
      rows.push(`Deck: ${this.formatChangeValue(changes.deck.from)} -> ${this.formatChangeValue(changes.deck.to)}`);
    }
    if (changes.source_path) {
      rows.push(
        `Source note: ${this.formatChangeValue(changes.source_path.from)} -> ${this.formatChangeValue(
          changes.source_path.to,
        )}`,
      );
    }
    if (changes.source_anchor) {
      rows.push(
        `Source anchor: ${this.formatChangeValue(changes.source_anchor.from)} -> ${this.formatChangeValue(
          changes.source_anchor.to,
        )}`,
      );
    }
    if (rows.length === 0) {
      return;
    }

    const metaEl = parent.createDiv({ cls: "corvus-preview-metadata" });
    metaEl.createEl("strong", { text: "Metadata changes" });
    const list = metaEl.createEl("ul");
    for (const row of rows) {
      list.createEl("li", { text: row });
    }
  }

  private formatChangeValue(value: string | null | undefined): string {
    const cleaned = (value ?? "").trim();
    return cleaned || "(empty)";
  }

  private renderCardFace(
    parent: HTMLElement,
    label: string,
    markdown: string,
    cls: string,
  ): void {
    const sectionEl = parent.createDiv({ cls: "corvus-preview-card-face" });
    sectionEl.createEl("div", {
      cls: "corvus-preview-card-face-label",
      text: label,
    });
    const bodyEl = sectionEl.createDiv({ cls });
    const renderChild = new MarkdownRenderChild(bodyEl);
    this.markdownChildren.push(renderChild);
    void MarkdownRenderer.render(this.app, markdown, bodyEl, "", renderChild);
  }

  private actionLabel(card: PreviewCard): string {
    if (card.errors.length > 0) {
      return "Invalid";
    }
    if (card.unchanged) {
      return "Unchanged";
    }
    if (card.existing) {
      return "Update";
    }
    return "Create";
  }
}
