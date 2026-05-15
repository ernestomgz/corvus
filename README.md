# Corvus SRS — Phase 1

Self-hosted spaced repetition system built with Django 5, HTMX, Tailwind, and PostgreSQL.

## Quickstart

```sh
git clone <repo-url>
cd Corvus
cp .env.example .env
# edit .env for local secrets if needed
docker compose up --build -d
docker compose exec web python manage.py migrate
```

Services:
- Web UI & API: http://localhost:8001
- PostgreSQL: localhost:5432 (`corvus` / `corvus` by default)

Optional demo data:
```sh
docker compose exec web python manage.py seed_demo  # demo user + sample deck
```

Credentials after seeding: `demo@example.com` / `demo1234`.

`docker compose up --build -d` starts the containers, but it does not create Django tables by itself. On a fresh database, run `python manage.py migrate` once so Django applies the current schema to PostgreSQL.

To fully reset local Docker state for this project, including the database and uploaded media volumes:

```sh
docker compose down -v --remove-orphans
docker compose up --build -d
docker compose exec web python manage.py migrate
```

This project is kept with clean initial migrations for fresh installs. It does not preserve old database state when you reset the Docker volumes.

Ready-made import bundles live in `samples/`:
- `sample_cards.zip` – Markdown/Logseq-style bundle with nested decks, tags, inline + block LaTeX, Obsidian media embeds, fenced code blocks, and URL examples.
- `sample.apkg` – Anki package with multi-deck Basic notes that mix inline/block LaTeX, HTML formatting, front/back images, and `[sound:...]` audio references.
Import them directly via the UI; no generation script is required. The included images and audio were created for this repository, so they can be redistributed without attribution.

## Tests

Inside the container:
```sh
docker compose exec web pytest
```
The suite covers SM-2 scheduling transitions, importer behaviours, permission boundaries, API review flow, and end-to-end import/re-import scenarios. (Tests were not executed in this workspace because Python is unavailable on the host; please run the command above.)

## Notable Features
- Email/password auth with custom `User` model (PBKDF2 hashes).
- Deck CRUD with HTMX-enhanced inline creation.
- Card browser with filtering, detail view, and editing.
- Review workflow implementing SM-2 defaults (Again/Hard/Good/Easy, leech tagging, learning/relearning queues).
- Markdown/Logseq ZIP importer (external ID detection, media copying, state-preserving upserts).
- Anki `.apkg` importer (SQLite parsing, media remapping, scheduling field mapping on new cards, idempotent re-imports).
- Public REST API (`/api/v1`) for auth, decks, cards, review flow, and imports.
- Tailwind CSS build baked into the Docker image; HTMX included via CDN.
- Knowledge maps: upload taxonomy JSON to `/api/knowledge-maps/import`, then tag cards with the generated `km:<map>:<node>` strings (see `docs/KNOWLEDGE_MAPS.md`).

## Useful Commands
- `docker compose exec web python manage.py createsuperuser`
- `docker compose exec web python manage.py seed_demo`
- `docker compose exec web python manage.py collectstatic --noinput`

## Project Structure Highlights
- `web/srs_app/settings.py` – environment-driven configuration.
- `web/core/scheduling.py` – SM-2 scheduler implementation.
- `web/core/services/review.py` – review queue helpers.
- `web/import_md/services.py`, `web/import_anki/services.py` – importer pipelines.
- `web/api/views.py` – session-authenticated JSON endpoints.
- `web/tests/` – pytest suite with factory_boy fixtures.

Enjoy building with Corvus! Contributions for later phases (export, richer analytics, etc.) can plug into the existing app structure.

## Knowledge Maps

Custom knowledge frameworks can be imported via JSON and managed through the API. Read `docs/KNOWLEDGE_MAPS.md` for the schema, tag format, and workflow, and explore `samples/knowledge_map.json` for a ready-to-import example. Tag cards with the generated `km:<map-slug>:<node-key>` strings to anchor them inside your map.
