from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Dict, List, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import Card, Deck, ExternalId, Import
from core.services.decks import ensure_deck_path
from core.scheduling import ensure_state, get_scheduler_config

IMG_PATTERN = re.compile(r'<img[^>]+src="([^"\s]+)"')
SOUND_PATTERN = re.compile(r'\[sound:([^\]]+)\]')


@dataclass
class ParsedAnkiCard:
    front_md: str
    back_md: str
    external_key: str
    source_path: str
    source_anchor: str
    media: list[dict]
    schedule: dict


class AnkiImportError(Exception):
    """Raised when the Anki importer cannot process a package."""


def _normalise_text(value: str) -> str:
    if not value:
        return ''
    value = value.replace('<br />', '\n').replace('<br>', '\n')
    return html.unescape(value)


def _ensure_media_directory(user_id: int) -> Path:
    root = Path(settings.MEDIA_ROOT)
    if root.exists() and not root.is_dir():
        root.unlink()
    root.mkdir(parents=True, exist_ok=True)
    path = root / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_media_file(user_id: int, source_path: Path, *, original_name: str | None = None) -> Tuple[str, dict]:
    data = source_path.read_bytes()
    digest = hashlib.sha1(data).hexdigest()
    ext = source_path.suffix
    filename = f"{digest}{ext}"
    dest_dir = _ensure_media_directory(user_id)
    dest_path = dest_dir / filename
    if not dest_path.exists():
        dest_path.write_bytes(data)
    url = f"{settings.MEDIA_URL}{user_id}/{filename}".replace('//', '/')
    meta = {'name': original_name or source_path.name, 'url': url, 'hash': digest}
    return url, meta


def _rewrite_media(value: str, *, user_id: int, media_dir: Path, media_map: Dict[str, str], summary: dict) -> Tuple[str, List[dict]]:
    media_items: list[dict] = []

    def _resolve_source(token: str) -> tuple[Path, str] | None:
        mapped_name = media_map.get(token)
        candidates: list[tuple[str, str]] = []
        if mapped_name:
            candidates.append((mapped_name, mapped_name))
        candidates.append((token, mapped_name or token))
        seen: set[Path] = set()
        for filename, display_name in candidates:
            if not filename:
                continue
            path = media_dir / filename
            if path.exists() and path not in seen:
                seen.add(path)
                return path, display_name
        return None

    def replace_img(match: re.Match) -> str:
        token = match.group(1)
        resolved = _resolve_source(token)
        if not resolved:
            return match.group(0)
        source_file, original_name = resolved
        url, meta = _copy_media_file(user_id, source_file, original_name=original_name)
        if meta not in media_items:
            media_items.append(meta)
            summary['media_copied'] += 1
        return match.group(0).replace(token, url)

    def replace_sound(match: re.Match) -> str:
        token = match.group(1)
        resolved = _resolve_source(token)
        if not resolved:
            return match.group(0)
        source_file, original_name = resolved
        url, meta = _copy_media_file(user_id, source_file, original_name=original_name)
        if meta not in media_items:
            media_items.append(meta)
            summary['media_copied'] += 1
        return match.group(0).replace(token, url)

    rewritten = IMG_PATTERN.sub(replace_img, value)
    rewritten = SOUND_PATTERN.sub(replace_sound, rewritten)
    return rewritten, media_items


def _load_media_map(temp_dir: Path) -> Dict[str, str]:
    media_json_path = temp_dir / 'media'
    if not media_json_path.exists():
        media_json_path = temp_dir / 'media.json'
    if not media_json_path.exists():
        return {}
    try:
        text = media_json_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        text = media_json_path.read_text(encoding='utf-8', errors='ignore')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _load_deck_paths(connection: sqlite3.Connection) -> Dict[int, list[str]]:
    try:
        row = connection.execute('SELECT decks FROM col').fetchone()
    except sqlite3.Error:
        return {}
    if not row or not row[0]:
        return {}
    try:
        data = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return {}
    deck_paths: Dict[int, list[str]] = {}
    for deck_id, meta in data.items():
        name = meta.get('name') if isinstance(meta, dict) else None
        if not name:
            continue
        parts = [part.strip() for part in name.split('::') if part.strip()]
        try:
            deck_paths[int(deck_id)] = parts
        except (TypeError, ValueError):
            continue
    return deck_paths


def _load_notes(connection: sqlite3.Connection) -> Dict[int, dict]:
    notes: Dict[int, dict] = {}
    cursor = connection.execute('SELECT id, guid, flds FROM notes')
    for note_id, guid, flds in cursor.fetchall():
        notes[note_id] = {'guid': guid, 'fields': flds.split('\x1f')}
    return notes


def _map_queue_status(queue: int) -> str:
    return {
        0: 'new',
        1: 'learn',
        2: 'review',
        3: 'relearn',
    }.get(queue, 'new')


@transaction.atomic
def process_apkg_archive(*, user, deck: Deck, uploaded_file) -> Import:
    summary = {'created': 0, 'updated': 0, 'skipped': 0, 'media_copied': 0, 'decks_created': 0}
    import_record = Import.objects.create(
        user=user,
        kind='anki',
        status='partial',
        summary=summary,
        created_at=timezone.now(),
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            with zipfile.ZipFile(uploaded_file) as zf:
                zf.extractall(tmpdir)

            collection_path = None
            for candidate in ('collection.anki21', 'collection.anki21b', 'collection.anki2'):
                possible = tmpdir / candidate
                if possible.exists():
                    collection_path = possible
                    break
            if not collection_path:
                raise AnkiImportError('collection.anki2 or collection.anki21 not found in package')

            connection = sqlite3.connect(collection_path)
            try:
                notes = _load_notes(connection)
                media_map = _load_media_map(tmpdir)
                deck_paths = _load_deck_paths(connection)
                deck_cache: dict[tuple[str, ...], Deck] = {(): deck}

                def resolve_deck(parts: list[str]) -> tuple[Deck, list[Deck]]:
                    key = tuple(parts)
                    if key in deck_cache:
                        return deck_cache[key], []
                    target, created = ensure_deck_path(user, deck, parts)
                    deck_cache[key] = target
                    return target, created

                cursor = connection.execute(
                    'SELECT id, nid, did, ord, ivl, reps, lapses, factor, due, queue FROM cards'
                )
                for card_id, note_id, deck_id, ord_num, ivl, reps, lapses, factor, due, queue in cursor.fetchall():
                    note = notes.get(note_id)
                    if not note:
                        summary['skipped'] += 1
                        continue

                    fields = note['fields']
                    front_raw = fields[0] if fields else ''
                    back_raw = fields[1] if len(fields) > 1 else ''

                    front_md = _normalise_text(front_raw)
                    back_md = _normalise_text(back_raw)
                    front_md, media_front = _rewrite_media(
                        front_md,
                        user_id=user.id,
                        media_dir=tmpdir,
                        media_map=media_map,
                        summary=summary,
                    )
                    back_md, media_back = _rewrite_media(
                        back_md,
                        user_id=user.id,
                        media_dir=tmpdir,
                        media_map=media_map,
                        summary=summary,
                    )
                    media: list[dict] = []
                    for item in media_front + media_back:
                        if item not in media:
                            media.append(item)

                    deck_parts = deck_paths.get(deck_id, [])
                    effective_parts = [part for part in deck_parts if part.strip() and part.strip().lower() != 'default']
                    if not effective_parts:
                        target_deck, created_decks = deck, []
                    else:
                        target_deck, created_decks = resolve_deck(effective_parts)
                    if created_decks:
                        summary['decks_created'] += len(created_decks)

                    external_key = f"{note['guid']}:{ord_num}"
                    source_anchor = str(card_id)

                    try:
                        external = ExternalId.objects.select_related('card').get(system='anki', external_key=external_key)
                    except ExternalId.DoesNotExist:
                        card = Card.objects.create(
                            user=user,
                            deck=target_deck,
                            front_md=front_md,
                            back_md=back_md,
                            tags=[],
                            media=media,
                            source_path=f"apkg:{note['guid']}",
                            source_anchor=source_anchor,
                        )
                        ExternalId.objects.create(card=card, system='anki', external_key=external_key)
                        summary['created'] += 1

                        state = ensure_state(card)
                        state.queue_status = _map_queue_status(queue)
                        state.interval_days = max(int(ivl), 0)
                        state.reps = int(reps)
                        state.lapses = int(lapses)
                        factor_value = int(factor) if factor else int(get_scheduler_config().initial_ease * 1000)
                        state.ease = max(1.3, factor_value / 1000)
                        state.due_at = _compute_due_at(queue, due)
                        state.learning_step_index = 0
                        state.save()
                        continue

                    card = external.card
                    if card.user != user:
                        summary['skipped'] += 1
                        continue

                    card.front_md = front_md
                    card.back_md = back_md
                    card.media = media
                    card.deck = target_deck
                    card.save(update_fields=['front_md', 'back_md', 'media', 'deck', 'updated_at'])
                    summary['updated'] += 1
            finally:
                connection.close()

        import_record.status = 'ok'
        import_record.summary = summary
        import_record.save(update_fields=['status', 'summary'])
        return import_record
    except (zipfile.BadZipFile, sqlite3.DatabaseError) as exc:  # pragma: no cover
        import_record.status = 'error'
        import_record.summary = {'error': str(exc)}
        import_record.save(update_fields=['status', 'summary'])
        raise AnkiImportError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        import_record.status = 'error'
        import_record.summary = {'error': str(exc)}
        import_record.save(update_fields=['status', 'summary'])
        raise

def _compute_due_at(queue: int, due_value: int) -> datetime | None:
    try:
        value = int(due_value)
    except (TypeError, ValueError):
        return None
    if value < 0:
        value = 0
    if queue == 1:
        try:
            dt = datetime.fromtimestamp(value, tz=dt_timezone.utc)
        except (OverflowError, OSError, ValueError):
            return timezone.now()
        return timezone.localtime(dt)
    if queue in (2, 3):
        safe_days = min(value, 999_999_999)
        return timezone.now() + timedelta(days=safe_days)
    if queue == 0:
        return None
    safe_days = min(value, 999_999_999)
    return timezone.now() + timedelta(days=safe_days)
