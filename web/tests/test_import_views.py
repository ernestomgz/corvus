import pytest
from django.urls import reverse

from core.models import Card

from .test_import_anki import _build_apkg
from .test_import_markdown import _build_zip

pytestmark = pytest.mark.django_db


def test_markdown_import_view(client, user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    client.force_login(user)
    archive = _build_zip({'note.md': '#card Question\n\nAnswer'})
    response = client.post(
        reverse('imports:markdown'),
        {'deck': str(deck.id), 'archive': archive},
        follow=True,
    )
    assert response.status_code == 200
    assert Card.objects.filter(deck__parent=deck, deck__name='note.md').exists()


def test_anki_import_view(client, user_factory, deck_factory, tmp_path):
    user = user_factory()
    deck = deck_factory(user=user)
    client.force_login(user)
    upload = _build_apkg(tmp_path, front='Front', back='Back')
    response = client.post(
        reverse('imports:anki'),
        {'deck': str(deck.id), 'package': upload},
        follow=True,
    )
    assert response.status_code == 200
    assert Card.objects.filter(deck=deck).exists()
