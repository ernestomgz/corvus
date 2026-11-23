import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import NoteSource


@pytest.mark.django_db(transaction=True)
def test_existing_notes_are_backfilled_as_questions(deck_factory, card_factory):
    MigrationExecutor(connection).migrate([('core', '0002_unique_top_level_deck_names')])
    try:
        root = deck_factory(name='STEM')
        note_deck = deck_factory(user=root.user, parent=root, name='BJT.md')
        card_factory(user=root.user, deck=note_deck, source_path='Electronics/BJT.md')
        card_factory(user=root.user, deck=note_deck, source_path='Electronics/BJT.md')
        card_factory(user=root.user, deck=note_deck)
    finally:
        MigrationExecutor(connection).migrate([('core', '0003_note_source')])

    source = NoteSource.objects.get(user=root.user)
    assert (source.source_root_deck_id, source.source_path, source.note_type) == (root.id, 'Electronics/BJT.md', 'questions')
