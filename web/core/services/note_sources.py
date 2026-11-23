import re

from ..models import Card, NoteSource, StudySet


def normalise_note_type(value):
    if not isinstance(value, str):
        raise ValueError('Note type must be text.')
    slug = re.sub(r'\s+', '-', value.strip().lower())
    if len(slug) > 64 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug):
        raise ValueError('Note type must use letters, numbers, spaces, or hyphens.')
    return slug


def note_type_label(note_type):
    return note_type.replace('-', ' ').capitalize()


def source_metadata(user, root, paths):
    return {source.source_path: source for source in NoteSource.objects.filter(
        user=user, source_root_deck=root, source_path__in=paths,
    )}


def resolve_note_type(existing, note_type, priority):
    if existing and existing.type_priority > priority:
        return existing.note_type, existing.type_priority
    return note_type, priority


def save_note_type(user, root, path, note_type, priority):
    # Both callers are inside the transaction that applies the preview.
    source, created = NoteSource.objects.select_for_update().get_or_create(
        user=user, source_root_deck=root, source_path=path,
        defaults={'note_type': note_type, 'type_priority': priority},
    )
    if not created:
        effective = resolve_note_type(source, note_type, priority)
        if effective != (source.note_type, source.type_priority):
            source.note_type, source.type_priority = effective
            source.save(update_fields=['note_type', 'type_priority'])


def preset_source_types(preset):
    if preset.kind != StudySet.KIND_CUSTOM or not preset.source_root_deck_id or not preset.source_paths:
        return {}
    saved = source_metadata(preset.user_id, preset.source_root_deck_id, preset.source_paths)
    return {path: saved[path].note_type if path in saved else NoteSource.QUESTIONS for path in preset.source_paths}


def preset_note_types(preset):
    types = preset_source_types(preset)
    if not types:
        return []
    populated = Card.objects.filter(
        user_id=preset.user_id, source_path__in=types,
        deck_id__in=preset.source_root_deck.descendant_ids(),
    ).order_by().values_list('source_path', flat=True).distinct()
    available = sorted({types[path] for path in populated}, key=lambda slug: (slug != NoteSource.QUESTIONS, slug))
    return [{'slug': slug, 'label': note_type_label(slug)} for slug in available]
