# How To Execute The Obsidian Plugin

## Prerequisites

You need:

- Obsidian desktop
- Node.js 18 or newer
- npm
- a Corvus instance running with the new Obsidian preview/apply endpoints

Corvus must be reachable from Obsidian, for example:

- `http://localhost:8000`

The configured Corvus root deck must already exist.

Each synced note gets its own deck below that root. For example, with `STEM` as the root, `Electronics/BJT.md` syncs to `STEM/Electronics/BJT.md`. Use the preview to review proposed deck moves after renaming or moving a note. After a linked note moves, rerun the study preset command to refresh its saved source paths.


## Build The Plugin

From the repository root:

```powershell
cd obsidian_plugin
npm install
npm run build
```

This generates:

- `main.js`

The plugin package files are:

- `manifest.json`
- `main.js`
- `styles.css`


## Install Into Obsidian

1. Open your Obsidian vault folder.
2. Go to `.obsidian/plugins/`.
3. Create a folder named `corvus-sync`.
4. Copy these files from `obsidian_plugin/` into that folder:
   - `manifest.json`
   - `main.js`
   - `styles.css`

Result:

```text
<your-vault>/.obsidian/plugins/corvus-sync/
  manifest.json
  main.js
  styles.css
```


## Enable The Plugin

1. Open Obsidian.
2. Go to `Settings -> Community plugins`.
3. Turn off Safe Mode if required.
4. Enable `Corvus Sync`.


## Configure The Plugin

Open:

- `Settings -> Corvus Sync`

Set:

- `Corvus Base URL`
- `Username / Email`
- `Password`
- `Root Deck Path`

Use `Test` to verify login works.


## Use The Plugin

1. Open a markdown note with `#card`, `#card-reverse`, `#long-card`, or `#long-card-reverse` markers.
2. Run the command:

```text
Corvus Sync: Sync Current Note
```

3. Review the preview modal.
4. Click `Apply Selected`.

If Corvus creates new cards without source IDs, the plugin writes those IDs back into the note.

### TikZ diagrams

Enable the **TikZJax** Obsidian community plugin to sync diagrams from backtick fences marked `tikz`. Corvus Sync exports the completed diagram as a PNG attachment. The original block stays in the note. An edit to the block can mark the card for update even when the resulting image looks the same.

If a block fails to render, Corvus Sync reports its note and line number. Open the note in Reading view and wait for the diagram before retrying. **Check Vault Sync** reports the failure on that note and continues checking the other notes.

## Check Vault Sync

Run **Corvus Sync: Check Vault Sync** from the command palette to see which notes need attention. Each row shows counts for matched IDs, updates, deck moves, and cards without IDs. Click a row to open the note, then run **Sync Current Note** if you want to apply changes.

To skip templates, open **Settings → Corvus Sync → Vault sync exclusions**. Enter one case-sensitive path from the vault root per line. For example, `Templates/` skips that folder and its subfolders, and `Study/Card Template.md` skips just that note. **Sync Current Note** remains available for those notes.

Use **Updates** for notes with pending changes, **Deck moves** for notes that would switch decks, and **ID issues** for duplicate or missing IDs and cards without IDs. Deck moves can also appear under Updates. **Up to date** shows notes with no pending changes or issues. Notes that could not be read appear at the top of **All**.

If the same ID appears in more than one note, the check lists the other note paths on each affected row. Review those notes before syncing, especially if one was copied with its card IDs intact.

The check sends candidate notes and their attachments to the configured Corvus server for comparison. It does not change notes, cards, decks, or stored media. The server must support `POST /api/v1/obsidian/audit-note`.

## Supported Frontmatter

The plugin and Corvus only check these kebab-case frontmatter keys. Other frontmatter keys can remain in your notes; they are ignored by Corvus.

- `questions-source`: read by the Obsidian plugin when creating or updating a custom study preset. It must contain one or more Obsidian wiki links to markdown notes.
- `card-tags`: read by Corvus. These tags are applied to every card imported from that markdown file.
- `card-heading-context`: read by Corvus. Set to `false` to stop Corvus from prepending the Markdown heading hierarchy to each imported card front.

`questions-source` is not used by `Corvus Sync: Sync Current Note`. Import the main note and the question note as separate sync actions. When creating a custom study preset, the plugin reads each note linked by the preset note and expands that note's `questions-source` into additional preset sources.

Preset note:

```md
# Test preset
[[Function]]
```

Linked note:

```md
---
created: 2026-05-23T16:03
last-modified: 2026-05-23T16:03
questions-source:"[[Questions Function]]"
card-heading-context: false
card-tags:
  - calculus
  - exam
---

Function
#card
Definition
```

The preset will include imported cards whose `source_path` is `Function.md` and `Questions Function.md`.

Example with multiple question source notes in a linked note:

```md
---
questions-source:
  - "[[Questions Limits]]"
  - "[[Questions Derivatives]]"
card-tags: calculus, practice
---
```

`questions-source` links must point to notes. Links to headings or blocks, such as `[[Questions#Section]]` or `[[Questions^block]]`, are rejected.


## Development Loop

For rebuild-on-change:

```powershell
cd obsidian_plugin
npm run dev
```

Then keep the plugin installed from the same folder contents or recopy `main.js` after rebuilds.


## Corvus Requirements

This plugin expects these Corvus endpoints to exist:

- `POST /api/v1/auth/login`
- `POST /api/v1/obsidian/preview`
- `GET /api/v1/obsidian/preview/<session_id>`
- `POST /api/v1/obsidian/preview/<session_id>/apply`
- `POST /api/v1/obsidian/preview/<session_id>/cancel`
- `POST /api/v1/obsidian/study-set/preview`
- `POST /api/v1/obsidian/study-set/apply`


## Notes

- The note sync command imports only the current note.
- The custom study preset command expands `questions-source` from notes linked by the preset note.
- Corvus remains responsible for markdown parsing, preview generation, media handling, and deck creation below the configured root.
- If the note changes after preview, the plugin refuses to write IDs back and asks you to run sync again.
