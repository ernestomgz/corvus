import json

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Card, Deck, ImportSession

pytestmark = pytest.mark.django_db


def _preview_payload(*, source_path: str, content: str, root_deck_path: str, source_hash: str) -> dict:
    return {
        'source_path': source_path,
        'content': content,
        'root_deck_path': root_deck_path,
        'source_hash': source_hash,
    }


def test_obsidian_preview_requires_auth(api_client):
    response = api_client.post('/api/v1/obsidian/preview', data={})
    assert response.status_code == 401


def test_obsidian_preview_create_and_fetch_with_media(api_client, user_factory, deck_factory):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    api_client.force_login(user)

    content = "# Exponential function\n## Graph\n#card\n![[exponential_graph.jpg]]"
    payload = _preview_payload(
        source_path='Math/Functions/exponential.md',
        content=content,
        root_deck_path='STEM',
        source_hash='hash-123',
    )
    payload['attachment_manifest'] = json.dumps(
        [{'field': 'attachment_0', 'path': 'Math/Functions/exponential_graph.jpg'}]
    )
    payload['attachment_0'] = SimpleUploadedFile(
        'exponential_graph.jpg',
        b'fake-image-bytes',
        content_type='image/jpeg',
    )

    response = api_client.post('/api/v1/obsidian/preview', data=payload)
    assert response.status_code == 201
    data = response.json()
    assert data['status'] == 'ready'
    assert data['source_hash'] == 'hash-123'
    assert data['root_deck_path'] == 'STEM'
    assert data['planned_decks'] == ['STEM/Math', 'STEM/Math/Functions']
    assert data['has_errors'] is False
    assert len(data['cards']) == 1

    card = data['cards'][0]
    assert card['marker_line'] == 3
    assert card['marker_kind'] == 'card'
    assert card['target_deck_path'] == 'STEM/Math/Functions'
    assert card['will_write_back_id'] is True
    assert settings.MEDIA_URL in card['back_md']

    detail_response = api_client.get(f"/api/v1/obsidian/preview/{data['session_id']}")
    assert detail_response.status_code == 200
    detail_data = detail_response.json()
    assert detail_data['session_id'] == data['session_id']
    assert detail_data['cards'][0]['target_deck_path'] == 'STEM/Math/Functions'


def test_obsidian_preview_apply_creates_cards_and_nested_decks(api_client, user_factory, deck_factory):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    api_client.force_login(user)

    preview_response = api_client.post(
        '/api/v1/obsidian/preview',
        data=_preview_payload(
            source_path='Math/Functions/exponential.md',
            content='## Limit\n#card\nA definition',
            root_deck_path='STEM',
            source_hash='hash-create',
        ),
    )
    assert preview_response.status_code == 201
    preview_data = preview_response.json()

    apply_response = api_client.post(
        f"/api/v1/obsidian/preview/{preview_data['session_id']}/apply",
        data=json.dumps(
            {
                'source_hash': 'hash-create',
                'decisions': [{'index': 0, 'action': 'apply'}],
            }
        ),
        content_type='application/json',
    )
    assert apply_response.status_code == 200
    apply_data = apply_response.json()
    assert apply_data['status'] == 'applied'
    assert apply_data['summary']['created'] == 1
    assert apply_data['summary']['updated'] == 0
    assert apply_data['summary']['failed'] == 0
    assert len(apply_data['cards']) == 1
    assert apply_data['cards'][0]['status'] == 'created'
    assert apply_data['cards'][0]['import_id']

    math = Deck.objects.get(user=user, parent=root, name='Math')
    functions = Deck.objects.get(user=user, parent=math, name='Functions')
    card = Card.objects.get(user=user)
    assert card.deck == functions
    assert card.import_id == apply_data['cards'][0]['import_id']


def test_obsidian_preview_apply_updates_existing_card_by_import_id(
    api_client,
    user_factory,
    deck_factory,
    card_factory,
):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    child = deck_factory(user=user, parent=root, name='Math')
    card = card_factory(user=user, deck=child, front_md='Old front', back_md='Old back', import_id='abc123')
    api_client.force_login(user)

    preview_response = api_client.post(
        '/api/v1/obsidian/preview',
        data=_preview_payload(
            source_path='Math/topic.md',
            content='## Updated front\n#card id:abc123\nUpdated back',
            root_deck_path='STEM',
            source_hash='hash-update',
        ),
    )
    assert preview_response.status_code == 201
    preview_data = preview_response.json()
    assert preview_data['cards'][0]['existing'] is True
    assert preview_data['cards'][0]['will_write_back_id'] is False

    apply_response = api_client.post(
        f"/api/v1/obsidian/preview/{preview_data['session_id']}/apply",
        data=json.dumps(
            {
                'source_hash': 'hash-update',
                'decisions': [{'index': 0, 'action': 'apply'}],
            }
        ),
        content_type='application/json',
    )
    assert apply_response.status_code == 200
    apply_data = apply_response.json()
    assert apply_data['summary']['updated'] == 1
    assert apply_data['cards'][0]['status'] == 'updated'

    card.refresh_from_db()
    assert card.import_id == 'abc123'
    assert 'Updated front' in card.front_md
    assert card.back_md == 'Updated back'


def test_obsidian_preview_apply_updates_source_path_and_deck_without_content_change(
    api_client,
    user_factory,
    deck_factory,
    card_factory,
):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    math = deck_factory(user=user, parent=root, name='Math')
    card = card_factory(
        user=user,
        deck=math,
        front_md='Question',
        back_md='Answer',
        import_id='abc123',
        source_path='Math/topic.md',
    )
    api_client.force_login(user)

    preview_response = api_client.post(
        '/api/v1/obsidian/preview',
        data=_preview_payload(
            source_path='Science/topic.md',
            content='Question\n#card id:abc123\nAnswer',
            root_deck_path='STEM',
            source_hash='hash-move',
        ),
    )
    assert preview_response.status_code == 201
    preview_data = preview_response.json()
    preview_card = preview_data['cards'][0]
    assert preview_data['summary']['updates'] == 1
    assert preview_data['summary']['unchanged'] == 0
    assert preview_data['planned_decks'] == ['STEM/Science']
    assert preview_card['existing'] is True
    assert preview_card['has_changes'] is True
    assert preview_card['unchanged'] is False
    assert preview_card['existing_deck_path'] == 'STEM/Math'
    assert preview_card['target_deck_path'] == 'STEM/Science'
    assert preview_card['metadata_changes']['deck'] == {
        'from': 'STEM/Math',
        'to': 'STEM/Science',
    }
    assert preview_card['metadata_changes']['source_path'] == {
        'from': 'Math/topic.md',
        'to': 'Science/topic.md',
    }

    apply_response = api_client.post(
        f"/api/v1/obsidian/preview/{preview_data['session_id']}/apply",
        data=json.dumps(
            {
                'source_hash': 'hash-move',
                'decisions': [{'index': 0, 'action': 'apply'}],
            }
        ),
        content_type='application/json',
    )
    assert apply_response.status_code == 200
    apply_data = apply_response.json()
    assert apply_data['summary']['updated'] == 1
    assert apply_data['summary']['decks_created'] == 1

    science = Deck.objects.get(user=user, parent=root, name='Science')
    card.refresh_from_db()
    assert card.deck == science
    assert card.source_path == 'Science/topic.md'
    assert card.front_md == 'Question'
    assert card.back_md == 'Answer'


def test_obsidian_preview_apply_rejects_source_hash_mismatch(api_client, user_factory, deck_factory):
    user = user_factory()
    deck_factory(user=user, name='STEM', parent=None)
    api_client.force_login(user)

    preview_response = api_client.post(
        '/api/v1/obsidian/preview',
        data=_preview_payload(
            source_path='Math/topic.md',
            content='## Topic\n#card\nAnswer',
            root_deck_path='STEM',
            source_hash='hash-preview',
        ),
    )
    assert preview_response.status_code == 201
    preview_data = preview_response.json()

    apply_response = api_client.post(
        f"/api/v1/obsidian/preview/{preview_data['session_id']}/apply",
        data=json.dumps({'source_hash': 'different-hash', 'decisions': []}),
        content_type='application/json',
    )
    assert apply_response.status_code == 409
    assert Card.objects.filter(user=user).count() == 0


def test_obsidian_preview_cancel_marks_session_cancelled(api_client, user_factory, deck_factory):
    user = user_factory()
    deck_factory(user=user, name='STEM', parent=None)
    api_client.force_login(user)

    preview_response = api_client.post(
        '/api/v1/obsidian/preview',
        data=_preview_payload(
            source_path='Math/topic.md',
            content='## Topic\n#card\nAnswer',
            root_deck_path='STEM',
            source_hash='hash-cancel',
        ),
    )
    session_id = preview_response.json()['session_id']

    cancel_response = api_client.post(f'/api/v1/obsidian/preview/{session_id}/cancel')
    assert cancel_response.status_code == 200
    assert cancel_response.json()['status'] == 'cancelled'

    session = ImportSession.objects.get(id=session_id)
    assert session.status == 'cancelled'


def test_obsidian_preview_rejects_unknown_root_deck(api_client, user_factory):
    user = user_factory()
    api_client.force_login(user)

    response = api_client.post(
        '/api/v1/obsidian/preview',
        data=_preview_payload(
            source_path='Math/topic.md',
            content='## Topic\n#card\nAnswer',
            root_deck_path='STEM',
            source_hash='hash-root',
        ),
    )
    assert response.status_code == 404
