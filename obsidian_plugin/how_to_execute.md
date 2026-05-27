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

- `questions-source`: read by the Obsidian plugin. It must contain one or more Obsidian wiki links to markdown notes. The plugin resolves those links with Obsidian and includes the referenced note(s) in the same sync preview.
- `card-tags`: read by Corvus. These tags are applied to every card imported from that markdown file.

Example with one question source note:

```md
---
created: 2026-05-23T16:03
last-modified: 2026-05-23T16:03
questions-source: "[[Questions Even and Odd functions]]"
card-tags:
  - calculus
  - exam
---

Even and odd functions
#card
Definitions and examples
```

Example with multiple question source notes:

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


## Notes

- The plugin syncs the current note and any markdown notes referenced by its `questions-source` frontmatter.
- Corvus remains responsible for markdown parsing, preview generation, media handling, and deck creation below the configured root.
- If the note changes after preview, the plugin refuses to write IDs back and asks you to run sync again.
