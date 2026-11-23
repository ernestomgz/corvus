import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from core.models import Card, Deck


@pytest.mark.django_db(transaction=True)
def test_top_level_deck_name_migration_preserves_existing_data(deck_factory, card_factory):
    target = [('core', '0002_unique_top_level_deck_names')]
    MigrationExecutor(connection).migrate([('core', '0001_initial')])

    first = deck_factory(name='Shared')
    second = deck_factory(user=first.user, name='Shared')
    card = card_factory(user=first.user, deck=second)
    state_id = card.scheduling_state.pk
    deck_factory(name='Shared')  # Another user's deck can share this name.
    deck_factory(user=first.user, parent=first, name='Shared')

    try:
        with pytest.raises(RuntimeError, match='Duplicate top-level deck names.*Shared'):
            MigrationExecutor(connection).migrate(target)
        assert Deck.objects.filter(pk__in=[first.pk, second.pk], name='Shared').count() == 2
        assert Card.objects.filter(pk=card.pk, deck=second).exists()
    finally:
        Deck.objects.filter(pk=second.pk).update(name='Shared archive')
        MigrationExecutor(connection).migrate([('core', '0003_note_source')])

    card.refresh_from_db()
    assert card.deck_id == second.pk
    assert card.scheduling_state.pk == state_id
    with pytest.raises(IntegrityError), transaction.atomic():
        Deck.objects.create(user=first.user, name='Shared')
