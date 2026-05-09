import pytest
from django.test import Client

from .factories import (
    CardFactory,
    DeckFactory,
    ExternalIdFactory,
    SchedulingStateFactory,
    UserFactory,
)


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    media_root = tmp_path / 'media'
    media_root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = media_root
    yield


@pytest.fixture
def user_factory():
    return UserFactory


@pytest.fixture
def deck_factory():
    return DeckFactory


@pytest.fixture
def card_factory():
    return CardFactory


@pytest.fixture
def scheduling_state_factory():
    return SchedulingStateFactory


@pytest.fixture
def external_id_factory():
    return ExternalIdFactory


@pytest.fixture
def api_client():
    return Client()
