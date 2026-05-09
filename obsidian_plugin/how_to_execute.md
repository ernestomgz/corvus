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

- The plugin syncs only the current note.
- Corvus remains responsible for markdown parsing, preview generation, media handling, and deck creation below the configured root.
- If the note changes after preview, the plugin refuses to write IDs back and asks you to run sync again.
