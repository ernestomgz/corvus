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
