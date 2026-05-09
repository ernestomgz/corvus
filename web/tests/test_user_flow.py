import pytest
from django.urls import reverse

from core.models import Card, Deck

pytestmark = pytest.mark.django_db


def test_end_to_end_user_flow(client):
    register_data = {
        'email': 'flow@example.com',
        'password': 'strong-pass-123',
    }
    response = client.post('/accounts/register/', register_data, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain
    assert '/decks/' in response.redirect_chain[-1][0]

    deck_payload = {
        'name': 'Integration Deck',
        'description': 'Flow testing deck',
    }
    response = client.post('/decks/', deck_payload, follow=True)
    assert response.status_code == 200
    deck = Deck.objects.get(name='Integration Deck')

    card_payload = {
        'deck': str(deck.id),
        'front_md': 'Front content',
        'back_md': 'Back content',
        'tags': 'flow,test',
    }
    response = client.post('/cards/create/', card_payload, follow=True)
    assert response.status_code == 200
    card = Card.objects.get(front_md='Front content')
    assert hasattr(card, 'scheduling_state')

    today_response = client.get('/review/today/')
    assert today_response.status_code == 200

    next_response = client.get('/review/next/')
    assert next_response.status_code == 200
    assert str(card.id) in next_response.content.decode()

    reveal_response = client.post('/review/reveal/', {'card_id': str(card.id)})
    assert reveal_response.status_code == 200

    grade_response = client.post('/review/grade/', {'card_id': str(card.id), 'rating': '2'})
    assert grade_response.status_code == 200
    card.refresh_from_db()

    delete_card_response = client.post(f'/cards/{card.id}/delete/', follow=True)
    assert delete_card_response.status_code == 200
    assert not Card.objects.filter(id=card.id).exists()

    delete_deck_response = client.post(f'/decks/{deck.id}/delete/', follow=True)
    assert delete_deck_response.status_code == 200
    assert not Deck.objects.filter(id=deck.id).exists()

    logout_response = client.get('/accounts/logout/', follow=True)
    assert logout_response.status_code == 200
    assert logout_response.request['PATH_INFO'] == reverse('accounts:login')
