import io
import yaml

import pytest
from django.urls import reverse

from core.models import UserSettings
from tests.factories import StudySetFactory

pytestmark = pytest.mark.django_db


def test_settings_create_and_save(client, user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    study_set = StudySetFactory(user=user)
    assert client.login(email=user.email, password='password123')

    url = reverse('settings:detail')
    response = client.get(url)
    assert response.status_code == 200

    payload = {
        'default_deck': deck.id,
        'default_study_set': study_set.id,
        'new_card_daily_limit': 15,
        'notifications_enabled': 'on',
        'theme': 'dark',
    }
    post = client.post(url, payload, follow=True)
    assert post.status_code == 200
    settings_obj = UserSettings.objects.get(user=user)
    assert settings_obj.default_deck_id == deck.id
    assert settings_obj.default_study_set_id == study_set.id
    assert settings_obj.new_card_daily_limit == 15
    assert settings_obj.notifications_enabled is True
    assert settings_obj.theme == 'dark'


def test_settings_export_contains_only_active_settings(client, user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    study_set = StudySetFactory(user=user)
    UserSettings.objects.create(
        user=user,
        default_deck=deck,
        default_study_set=study_set,
        new_card_daily_limit=12,
        notifications_enabled=True,
        theme='light',
    )
    client.force_login(user)
    response = client.get(reverse('settings:export'))
    assert response.status_code == 200
    assert response['Content-Type'].startswith('text/yaml')
    payload = yaml.safe_load(io.StringIO(response.content.decode('utf-8')))
    assert payload == {
        'default_deck_id': deck.id,
        'default_study_set_id': study_set.id,
        'new_card_daily_limit': 12,
        'notifications_enabled': True,
        'theme': 'light',
    }
