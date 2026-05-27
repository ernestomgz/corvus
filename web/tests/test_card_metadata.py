import pytest
from django.urls import reverse

from tests.factories import ExternalIdFactory

pytestmark = pytest.mark.django_db


def test_card_detail_page_displays_source_metadata(client, user_factory, deck_factory, card_factory):
    user = user_factory()
    deck = deck_factory(user=user, name='Science')
    card = card_factory(
        user=user,
        deck=deck,
        source_path='notes/Science/math.md',
        source_anchor='math-card',
        tags=['algebra'],
    )
    ExternalIdFactory(card=card, system='logseq', external_key='notes/Science/math.md:3')
    assert client.login(email=user.email, password='password123')

    response = client.get(reverse('cards:detail', kwargs={'pk': card.id}))

    assert response.status_code == 200
    body = response.content.decode('utf-8')
    assert 'Source note' in body
    assert 'notes/Science/math.md' in body
    assert 'Source anchor' in body
    assert 'math-card' in body
    assert 'Card UUID' in body
    assert str(card.id) in body
    assert 'External IDs' in body
    assert 'notes/Science/math.md:3' in body


def test_card_api_detail_includes_source_and_deck_metadata(api_client, user_factory, deck_factory, card_factory):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    deck = deck_factory(user=user, name='Science', parent=root)
    card = card_factory(user=user, deck=deck, source_path='notes/Science/math.md', source_anchor='math-card')
    api_client.force_login(user)

    response = api_client.get(f'/api/v1/cards/{card.id}')

    assert response.status_code == 200
    data = response.json()
    assert data['id'] == str(card.id)
    assert data['deck_id'] == deck.id
    assert data['deck_full_path'] == 'STEM/Science'
    assert data['source_path'] == 'notes/Science/math.md'
    assert data['source_anchor'] == 'math-card'
    assert 'created_at' in data
    assert 'updated_at' in data
