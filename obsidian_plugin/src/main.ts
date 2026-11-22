import { App, Notice, Plugin, PluginSettingTab, Setting } from "obsidian";

import { CorvusClient } from "./corvus-client";
import { collectCurrentNoteSource, collectCurrentStudySetSource, collectNoteSource } from "./note-source";
import { SyncPreviewModal } from "./preview-modal";
import { StudySetPreviewModal } from "./study-set-modal";
import { createVaultSyncExclusionMatcher } from "./vault-sync-exclusions";
import { VaultSyncModal, type VaultSyncRow } from "./vault-sync-modal";
import { DEFAULT_SETTINGS, type CorvusPluginSettings, type NoteSyncSource } from "./types";
import { writeBackImportIds } from "./writeback";

export default class CorvusSyncPlugin extends Plugin {
  settings: CorvusPluginSettings = { ...DEFAULT_SETTINGS };
  private checkingVault = false;

  async onload(): Promise<void> {
    await this.loadSettings();

    this.addSettingTab(new CorvusSyncSettingTab(this.app, this));

    this.addCommand({
      id: "sync-current-note",
      name: "Sync Current Note",
      callback: async () => {
        await this.syncCurrentNote();
      },
    });

    this.addCommand({
      id: "check-vault-sync",
      name: "Check Vault Sync",
      callback: async () => {
        await this.checkVaultSync();
      },
    });

    this.addCommand({
      id: "create-study-preset-from-current-note",
      name: "Create Custom Study Preset From Current Note",
      callback: async () => {
        await this.createStudyPresetFromCurrentNote();
      },
    });

    this.addCommand({
      id: "test-connection",
      name: "Test Corvus Connection",
      callback: async () => {
        await this.testConnection();
      },
    });
  }

  async checkVaultSync(): Promise<void> {
    if (this.checkingVault) return;

    this.checkingVault = true;
    const progress = new Notice("Connecting to Corvus…", 0);
    try {
      this.assertConfigured();
      const client = new CorvusClient(this.settings);
      await client.login();

      const isExcluded = createVaultSyncExclusionMatcher(this.settings.vaultSyncExclusions);
      const notes = this.app.vault.getMarkdownFiles().filter((file) => !isExcluded(file.path));
      const rows: VaultSyncRow[] = [];
      for (const [index, file] of notes.entries()) {
        progress.setMessage(`Checking Corvus sync · ${index + 1} of ${notes.length} notes`);
        let source: NoteSyncSource;
        try {
          const content = await this.app.vault.cachedRead(file);
          // The server parser decides whether these markers actually produce cards.
          if (!/#(?:long-)?card(?:-reverse)?(?=\s|$)/i.test(content)) continue;
          source = await collectNoteSource(this.app, file, content);
        } catch (error) {
          rows.push({ file, error: this.errorMessage(error, "Could not read note") });
          continue;
        }
        const audit = await client.auditNote(source);
        if (audit.identified || audit.without_ids || audit.errors.length) rows.push({ file, audit });
      }
      new VaultSyncModal(this.app, rows).open();
    } catch (error) {
      const message = this.errorMessage(error, "Corvus vault check failed");
      console.error(message);
      new Notice(message);
    } finally {
      progress.hide();
      this.checkingVault = false;
    }
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  async testConnection(): Promise<void> {
    try {
      this.assertConfigured();
      const client = new CorvusClient(this.settings);
      await client.login();
      new Notice("Corvus connection succeeded.");
    } catch (error) {
      new Notice(this.errorMessage(error));
    }
  }

  async syncCurrentNote(): Promise<void> {
    let client: CorvusClient | null = null;
    let previewSessionId: string | null = null;

    try {
      this.assertConfigured();
      client = new CorvusClient(this.settings);

      new Notice("Preparing Corvus preview...");
      await client.login();

      const source = await collectCurrentNoteSource(this.app);
      const preview = await client.createPreview(source);
      previewSessionId = preview.session_id;

      const decisions = await new SyncPreviewModal(this.app, preview).openAndWait();
      if (!decisions) {
        await client.cancelPreview(previewSessionId);
        new Notice("Corvus sync cancelled.");
        return;
      }

      const result = await client.applyPreview(preview.session_id, source.sourceHash, decisions);
      const writeCount = await writeBackImportIds(this.app, source.file, preview, result);

      new Notice(
        `Corvus sync finished. Created ${result.summary.created}, updated ${result.summary.updated}, ` +
          `skipped ${result.summary.skipped}, wrote ${writeCount} ID line(s).`,
      );
    } catch (error) {
      if (client && previewSessionId) {
        try {
          await client.cancelPreview(previewSessionId);
        } catch {
          // Best effort cleanup; the original error is more important.
        }
      }
      new Notice(this.errorMessage(error));
      console.error("Corvus sync failed", error);
    }
  }

  async createStudyPresetFromCurrentNote(): Promise<void> {
    try {
      this.assertConfigured();
      const client = new CorvusClient(this.settings);

      new Notice("Preparing Corvus study preset preview...");
      await client.login();

      const source = await collectCurrentStudySetSource(this.app);
      const preview = await client.previewStudySet(source);
      if (preview.missing.length > 0) {
        new Notice("Some linked notes have not been imported into Corvus. Review the preview.");
      }

      const confirmed = await new StudySetPreviewModal(this.app, preview).openAndWait();
      if (!confirmed) {
        new Notice("Corvus study preset cancelled.");
        return;
      }

      const result = await client.applyStudySet(source);
      new Notice(
        `Corvus study preset ${result.action}: ${result.study_set.name} ` +
          `(${result.study_set.source_paths.length} source note(s)).`,
      );
    } catch (error) {
      new Notice(this.errorMessage(error, "Corvus study preset failed"));
      console.error("Corvus study preset failed", error);
    }
  }

  private assertConfigured(): void {
    if (!this.settings.baseUrl.trim()) {
      throw new Error("Set Corvus Base URL in the plugin settings.");
    }
    if (!this.settings.username.trim()) {
      throw new Error("Set Corvus Username / Email in the plugin settings.");
    }
    if (!this.settings.password.trim()) {
      throw new Error("Set Corvus Password in the plugin settings.");
    }
    if (!this.settings.rootDeckPath.trim()) {
      throw new Error("Set Corvus Root Deck Path in the plugin settings.");
    }
  }

  private errorMessage(error: unknown, fallback = "Corvus sync failed"): string {
    if (error instanceof Error && error.message.trim()) {
      return `${fallback}: ${error.message}`;
    }
    return `${fallback}.`;
  }
}

class CorvusSyncSettingTab extends PluginSettingTab {
  constructor(app: App, private readonly plugin: CorvusSyncPlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    containerEl.createEl("h2", { text: "Corvus Sync" });

    new Setting(containerEl)
      .setName("Corvus Base URL")
      .setDesc("Example: http://localhost:8000")
      .addText((text) =>
        text
          .setPlaceholder("http://localhost:8000")
          .setValue(this.plugin.settings.baseUrl)
          .onChange(async (value) => {
            this.plugin.settings.baseUrl = value.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Username / Email")
      .setDesc("Credentials used for the Corvus session login.")
      .addText((text) =>
        text.setValue(this.plugin.settings.username).onChange(async (value) => {
          this.plugin.settings.username = value.trim();
          await this.plugin.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName("Password")
      .setDesc("Stored locally in the Obsidian plugin settings.")
      .addText((text) => {
        text.inputEl.type = "password";
        text.setPlaceholder("Password");
        text.setValue(this.plugin.settings.password);
        text.onChange(async (value) => {
          this.plugin.settings.password = value;
          await this.plugin.saveSettings();
        });
      });

    new Setting(containerEl)
      .setName("Root Deck Path")
      .setDesc("Existing Corvus root deck path, for example STEM")
      .addText((text) =>
        text
          .setPlaceholder("STEM")
          .setValue(this.plugin.settings.rootDeckPath)
          .onChange(async (value) => {
            this.plugin.settings.rootDeckPath = value.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Vault sync exclusions")
      .setDesc("One path from the vault root per line. Include .md for notes. Folders include subfolders. Applies to Check Vault Sync.")
      .addTextArea((text) => {
        text.inputEl.rows = 4;
        text
          .setPlaceholder("Templates/\nStudy/Card Template.md")
          .setValue(this.plugin.settings.vaultSyncExclusions)
          .onChange(async (value) => {
            this.plugin.settings.vaultSyncExclusions = value;
            await this.plugin.saveSettings();
          });
      });

    new Setting(containerEl)
      .setName("Test Connection")
      .setDesc("Attempts a Corvus login using the current settings.")
      .addButton((button) =>
        button.setButtonText("Test").onClick(async () => {
          await this.plugin.testConnection();
        }),
      );
  }
}
