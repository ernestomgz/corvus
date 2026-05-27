# Corvus Obsidian Plugin Design

## Objective

Build an Obsidian plugin that syncs the current note to Corvus using a server-driven preview and apply flow.

The plugin should stay small. Corvus should remain responsible for markdown parsing, card extraction, preview generation, media rewriting, deck-path resolution, and final persistence.

This version is push-only:

- sync only the current note
- support only `#card`, `#card-reverse`, `#long-card`, and `#long-card-reverse`
- require approval before any write
- treat Obsidian IDs as the source of truth
- write newly assigned Corvus IDs back into the note after apply


## Core Design Decision

Do not make the plugin reimplement Corvus' markdown importer.

Do not make the plugin build a ZIP for every sync.

Instead:

- the plugin sends the current note as raw markdown, together with note metadata and any referenced local attachments
- Corvus creates a preview session from that raw note
- the plugin renders the preview in Obsidian
- after approval, Corvus applies the session
- the plugin patches new IDs back into the note

This is the cleanest split of responsibilities:

- plugin owns editor integration and user approval
- Corvus owns import semantics


## Why This Is Better

### Good reuse

Corvus already has the hard part:

- markdown card parsing
- heading-based front generation
- reverse-card handling
- import ID handling
- deck path creation
- preview sessions
- diff generation

The right design is to refactor that logic into reusable services and expose it through plugin-friendly APIs.

### Good boundaries

The plugin should not:

- parse card structure
- interpret LaTeX
- build front/back fields
- rewrite media URLs
- classify create vs update
- recreate deck resolution logic

Those are Corvus concerns.

### Good transport

A ZIP archive is a useful import format for manual web uploads. It is not a good primary transport for a note-sync plugin.

For a current-note workflow, the explicit transport should be:

- note content
- note path
- root deck
- referenced local attachments

That is clearer, easier to validate, and easier to evolve.


## Version 1 Scope

### In scope

- Obsidian plugin settings:
  - Corvus base URL
  - username/email
  - password
  - root deck path
- Sync current note only
- Preview inside Obsidian before apply
- Per-card selection in the preview
- Create and update cards in Corvus
- Minimal note writeback for new IDs
- Support local images referenced from the current note

### Out of scope

- Syncing multiple notes or whole folders
- Pulling changes from Corvus to Obsidian
- Delete tracking
- Background sync
- Tags, study sets, knowledge maps, analytics
- Client-side markdown parsing beyond what is needed to locate marker lines for ID writeback


## Ownership Split

## Plugin responsibilities

- Store user settings
- Authenticate to Corvus
- Read the active note and its vault-relative path
- Collect referenced local attachments from the current note
- Create a preview request
- Render preview and approval UI in Obsidian
- Apply selected changes
- Patch new IDs into the note after successful apply
- Detect if the note changed between preview and apply

## Corvus responsibilities

- Parse markdown note content
- Detect `#card`, `#card-reverse`, `#long-card`, and `#long-card-reverse`
- Extract and validate import IDs
- Build card previews and diffs
- Determine create vs update vs unchanged
- Resolve the target deck path from root deck + note folder path
- Create missing child decks when needed
- Resolve note-local media references against uploaded attachments
- Rewrite media links to Corvus media URLs
- Apply the import session
- Return assigned IDs for note writeback


## Supported Note Format

Version 1 supports only these marker forms:

- `#card`
- `#card id:<value>`
- `#card-reverse`
- `#card-reverse id:<value>`
- `#long-card`
- `#long-card id:<value>`
- `#long-card-reverse`
- `#long-card-reverse id:<value>`

The ID format should remain hexadecimal, aligned with Corvus `import_id`.

Corvus is responsible for validating marker syntax and card structure.


## Deck Mapping

Deck mapping should follow the same logic as the existing Corvus markdown import.

Given:

- root deck path from plugin settings, for example `STEM`
- note path `Math/Functions/exponential.md`

The target deck path is:

- `STEM/Math/Functions`

Rules:

- the note file name does not create a deck
- only folder segments under the note path are appended
- only the deck path needed for the current note is created
- the configured root deck must already exist
- Corvus may create missing child decks below that root


## Media Handling

The plugin should not upload a ZIP.

Instead, it should send the current note plus referenced local attachment files in one multipart request.

The plugin is not responsible for rewriting markdown media links. It only needs to provide:

- raw note markdown
- note path
- referenced attachment files
- mapping metadata for those files

Corvus should resolve image references using the same rules it already uses for markdown imports:

- markdown image syntax
- Obsidian wiki embeds
- note-relative and attachment-folder lookups

Corvus should then:

- copy media into user storage
- rewrite markdown references in the preview/apply pipeline
- persist final media metadata on the cards


## Identity Rules

- If the marker has `id:<value>`, that ID is authoritative.
- If Corvus finds a card with that `import_id`, the card is updated.
- If the note has no ID, Corvus creates a new card and assigns an `import_id`.
- If the note has an explicit ID and Corvus does not find it, Corvus should create the card using that explicit ID.
- If two markers in the same note have the same ID, preview must fail validation and apply must be blocked.

This keeps the source of truth simple:

- Obsidian owns the marker ID
- Corvus stores the same ID in `import_id`


## Approval Flow

No write should happen without approval.

The plugin should present one preview modal per sync run.

The preview should show:

- cards to create
- cards to update
- cards unchanged
- validation errors
- planned deck creations
- IDs that will be written back into the note after apply

Each preview item should include:

- action
- import ID or `(new)`
- front preview
- target deck path
- warning if the card already exists and will be overwritten

Users should be able to deselect individual cards before apply.


## Plugin Workflow

1. User runs `Corvus: Sync Current Note`.
2. Plugin reads the active note, note path, and note revision hash.
3. Plugin collects referenced local attachments for that note.
4. Plugin sends a preview request to Corvus.
5. Corvus creates a preview session and returns preview JSON.
6. Plugin shows the preview.
7. User approves selected items.
8. Plugin verifies the note has not changed since preview.
9. Plugin sends an apply request with selected decisions.
10. Corvus applies the session and returns final results, including assigned IDs.
11. Plugin writes new IDs back into the note with minimal edits.
12. Plugin shows a result summary.


## Corvus API Design

The plugin should not call low-level card CRUD endpoints directly for note sync.

Instead, Corvus should expose note-sync session endpoints.

## Endpoint 1: create preview session

`POST /api/v1/obsidian/preview`

Content type:

- `multipart/form-data`

Required fields:

- `source_path`: vault-relative note path, for example `Math/Functions/exponential.md`
- `content`: raw markdown note text
- `root_deck_path`: full Corvus root deck path, for example `STEM`
- `source_hash`: hash of the note text used for optimistic safety

Optional fields:

- attachment files
- attachment manifest describing each attachment's vault-relative path

Example conceptual request:

```text
source_path=Math/Functions/exponential.md
root_deck_path=STEM
source_hash=<sha256 of note content>
content=<raw markdown>
attachment_manifest=[...]
attachment_0=<binary>
attachment_1=<binary>
```

Response should include:

- `session_id`
- `source_hash`
- `summary`
- `cards`
- `planned_decks`
- `has_errors`

Each card item should include:

- `index`
- `marker_line`
- `marker_kind`
- `import_id`
- `front_md`
- `back_md`
- `existing`
- `has_changes`
- `warnings`
- `errors`
- `target_deck_path`
- `will_write_back_id`

## Endpoint 2: fetch preview session

`GET /api/v1/obsidian/preview/<session_id>`

This lets the plugin reopen an existing preview if needed.

## Endpoint 3: apply preview session

`POST /api/v1/obsidian/preview/<session_id>/apply`

Request:

```json
{
  "source_hash": "<same hash used during preview>",
  "decisions": [
    {"index": 0, "action": "apply"},
    {"index": 1, "action": "skip"}
  ]
}
```

Response:

```json
{
  "summary": {
    "created": 2,
    "updated": 1,
    "skipped": 1,
    "failed": 0,
    "decks_created": 1
  },
  "cards": [
    {
      "index": 0,
      "status": "created",
      "import_id": "1a2b",
      "card_id": "<uuid>",
      "marker_line": 12
    }
  ]
}
```

The response must include enough information for note writeback.

## Endpoint 4: cancel preview session

`POST /api/v1/obsidian/preview/<session_id>/cancel`

Optional, but useful for cleanup and explicit lifecycle control.


## Corvus Changes Required

The current Corvus codebase is close, but the server-side import pipeline is still tied to archive upload semantics.

To support the plugin cleanly, Corvus should be changed in these ways.

## 1. Refactor the markdown import service to separate transport from parsing

Today, markdown preview logic is built around uploaded files and ZIP archives.

Corvus should extract a lower-level service that works from structured sources rather than from archive files.

Recommended internal split:

- transport layer:
  - web ZIP upload
  - API raw-note upload for Obsidian
- shared domain layer:
  - markdown parsing
  - media resolution
  - session building
  - diff generation
  - apply logic

Good target shape:

- `prepare_markdown_session_from_archive(...)`
- `prepare_markdown_session_from_note(...)`
- both reuse a shared internal builder

This is the most important backend change.

## 2. Add a raw-note media resolver

The current importer resolves media by reading from a ZIP archive.

Corvus needs a second resolver that works from:

- note source path
- note content
- uploaded attachment manifest
- uploaded attachment file objects

This resolver should mimic existing archive behavior so preview and apply stay consistent across both import paths.

## 3. Reuse the existing preview session model

Corvus already has `ImportSession`.

That model should continue to back the plugin flow.

Recommended approach:

- keep using markdown import sessions
- record `source_system: obsidian` inside session payload or metadata
- do not create a second competing preview model unless truly necessary

## 4. Expose session payload as JSON

The current preview flow is rendered as HTML.

The plugin needs the same preview data as JSON.

Corvus should expose:

- create preview session
- fetch preview session
- apply preview session
- cancel preview session

These endpoints should use the same session payload that the web preview page already uses, extended with plugin-specific metadata such as marker line numbers and source hash.

## 5. Include stable writeback metadata in preview and apply responses

For safe note patching, Corvus should return:

- preview item index
- marker line number
- marker kind
- final `import_id`

Without this, the plugin would have to re-parse note structure locally, which defeats the design goal.

## 6. Validate root deck explicitly

Corvus should resolve `root_deck_path` to an existing deck.

Behavior:

- if root deck path does not exist, preview returns a validation error
- Corvus may create missing child decks under that root during apply
- Corvus should never silently create the configured root deck for the plugin

## 7. Add note hash safety checks

The preview request includes `source_hash`.

The apply request must include the same hash.

Corvus should store that hash in the preview session and reject apply if it does not match.

This prevents applying a stale preview after the user edited the note.


## Plugin Architecture

The plugin can stay small.

## Main modules

### Settings

- base URL
- username/email
- password
- root deck path

### Corvus client

- login
- create preview session
- fetch preview session
- apply preview session
- cancel preview session

### Note source collector

- read active note content
- compute source hash
- collect referenced local attachments using Obsidian vault metadata APIs

### Preview UI

- render session data from Corvus
- allow item selection
- surface warnings and errors

### Writeback module

- patch only marker lines that need new IDs
- do not reformat unrelated content
- abort if the note changed after preview


## Note Writeback Rules

Only patch markers that were created without an ID and received one from Corvus.

Minimal edit rules:

- `#card` becomes `#card id:<newid>`
- `#card-reverse` becomes `#card-reverse id:<newid>`
- `#long-card` becomes `#long-card id:<newid>`
- `#long-card-reverse` becomes `#long-card-reverse id:<newid>`
- do not rewrite surrounding text
- do not reorder content
- do not touch existing IDs

The plugin should re-read the note before patching and verify the content hash still matches the preview session.

If the note changed:

- do not patch
- tell the user to run preview again


## Error Handling

Preview-blocking errors:

- no active markdown note
- missing settings
- authentication failure
- invalid root deck
- duplicate IDs in the note
- invalid marker syntax
- missing referenced attachment file
- Corvus parsing/validation failure

Apply-time behavior:

- allow partial success
- return per-card results
- write back only IDs for cards that were actually created successfully


## Security

Version 1 may store username/password in plugin settings because that is the current requested auth model.

However, the design should keep the auth client isolated so Corvus can later move to API tokens without changing the rest of the plugin architecture.


## Implementation Plan

## Phase 1: Corvus refactor

- extract shared markdown session builder
- add raw-note input path
- add raw-note media resolver
- add JSON preview/apply endpoints
- include marker-line and source-hash metadata

## Phase 2: Plugin skeleton

- Obsidian plugin project
- settings tab
- Corvus client
- sync current note command

## Phase 3: Preview integration

- source collector
- preview request
- preview modal
- selection support

## Phase 4: Apply and writeback

- apply request
- note hash verification
- ID writeback
- result summary

## Phase 5: Hardening

- error handling
- partial success behavior
- session cancel support
- end-to-end tests against Corvus preview/apply APIs


## Acceptance Criteria

Version 1 is done when:

1. The plugin syncs only the current note.
2. The plugin does not parse cards itself.
3. Corvus generates the preview and final import behavior.
4. The preview is shown in Obsidian before apply.
5. Only needed child decks are created under the configured root deck.
6. Existing IDs update existing Corvus cards.
7. Missing IDs are assigned by Corvus and written back into the note.
8. The plugin does not require building a ZIP archive.
9. Media referenced by the current note can be included without separate manual upload.
10. A stale preview cannot be applied after the note changes.


## Final Recommendation

Keep the plugin thin and move note-sync semantics into Corvus.

The best production design is:

- plugin sends note source and attachments
- Corvus owns preview and apply
- plugin owns approval UI and minimal note writeback

That is simpler, more maintainable, and more correct than duplicating importer logic or using ZIP upload as the primary integration path.
