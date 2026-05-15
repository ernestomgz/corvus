import json

import pytest

from core.models import StudySet

pytestmark = pytest.mark.django_db


def _study_set_payload(*, name='Math', root_deck_path='STEM', links=None) -> dict:
    return {
        'name': name,
        'source_path': f'{name}.md',
        'source_hash': 'study-set-hash',
        'root_deck_path': root_deck_path,
        'links': links
        if links is not None
        else [
            {'link_text': 'Operations', 'obsidian_path': 'Operations.md'},
            {'link_text': 'Calculus', 'obsidian_path': 'Calculus.md'},
        ],
    }


def _post_json(api_client, path: str, payload: dict):
    return api_client.post(path, data=json.dumps(payload), content_type='application/json')


def test_obsidian_study_set_preview_requires_auth(api_client):
    response = _post_json(api_client, '/api/v1/obsidian/study-set/preview', _study_set_payload())
    assert response.status_code == 401


def test_obsidian_study_set_preview_rejects_unknown_root_deck(api_client, user_factory):
    user = user_factory()
    api_client.force_login(user)

    response = _post_json(api_client, '/api/v1/obsidian/study-set/preview', _study_set_payload())

    assert response.status_code == 404
    assert response.json()['error'] == 'root deck not found'


def test_obsidian_study_set_preview_resolves_linked_note_paths_to_decks(api_client, user_factory, deck_factory):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    operations = deck_factory(user=user, name='Operations', parent=root)
    algebra = deck_factory(user=user, name='Algebra', parent=root)
    polynomials = deck_factory(user=user, name='Polynomials', parent=algebra)
    api_client.force_login(user)

    response = _post_json(
        api_client,
        '/api/v1/obsidian/study-set/preview',
        _study_set_payload(
            links=[
                {'link_text': 'Operations', 'obsidian_path': 'Operations.md'},
                {'link_text': 'Algebra/Polynomials', 'obsidian_path': 'Algebra/Polynomials.md'},
            ]
        ),
    )

    assert response.status_code == 200
    data = response.json()
    assert data['name'] == 'Math'
    assert data['root_deck_path'] == 'STEM'
    assert data['action'] == 'create'
    assert data['will_update'] is False
    assert data['has_errors'] is False
    assert data['summary'] == {'deck_count': 2, 'missing_count': 0}
    assert data['decks'] == [
        {
            'id': operations.id,
            'name': 'Operations',
            'full_path': 'STEM/Operations',
            'obsidian_path': 'Operations.md',
            'link_text': 'Operations',
            'target_deck_path': 'STEM/Operations',
        },
        {
            'id': polynomials.id,
            'name': 'Polynomials',
            'full_path': 'STEM/Algebra/Polynomials',
            'obsidian_path': 'Algebra/Polynomials.md',
            'link_text': 'Algebra/Polynomials',
            'target_deck_path': 'STEM/Algebra/Polynomials',
        },
    ]


def test_obsidian_study_set_apply_creates_custom_preset(api_client, user_factory, deck_factory):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    operations = deck_factory(user=user, name='Operations', parent=root)
    calculus = deck_factory(user=user, name='Calculus', parent=root)
    api_client.force_login(user)

    response = _post_json(api_client, '/api/v1/obsidian/study-set/apply', _study_set_payload())

    assert response.status_code == 201
    data = response.json()
    assert data['status'] == 'applied'
    assert data['action'] == 'created'
    assert data['study_set']['name'] == 'Math'
    assert data['study_set']['kind'] == StudySet.KIND_CUSTOM
    assert data['study_set']['deck_ids'] == [operations.id, calculus.id]

    study_set = StudySet.objects.get(user=user, name='Math')
    assert study_set.kind == StudySet.KIND_CUSTOM
    assert set(study_set.decks.values_list('id', flat=True)) == {operations.id, calculus.id}
    assert study_set.tags == []
    assert study_set.filenames == []


def test_obsidian_study_set_apply_updates_existing_preset_by_name(api_client, user_factory, deck_factory):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    old_deck = deck_factory(user=user, name='Old', parent=root)
    geometry = deck_factory(user=user, name='Geometry', parent=root)
    existing = StudySet.objects.create(
        user=user,
        name='Math',
        kind=StudySet.KIND_CUSTOM,
        tags=['old-tag'],
        filenames=['old.md'],
    )
    existing.decks.add(old_deck)
    api_client.force_login(user)

    preview_response = _post_json(
        api_client,
        '/api/v1/obsidian/study-set/preview',
        _study_set_payload(links=[{'link_text': 'Geometry', 'obsidian_path': 'Geometry.md'}]),
    )
    assert preview_response.status_code == 200
    assert preview_response.json()['will_update'] is True

    apply_response = _post_json(
        api_client,
        '/api/v1/obsidian/study-set/apply',
        _study_set_payload(links=[{'link_text': 'Geometry', 'obsidian_path': 'Geometry.md'}]),
    )

    assert apply_response.status_code == 200
    data = apply_response.json()
    assert data['action'] == 'updated'
    assert data['study_set']['id'] == existing.id
    assert data['study_set']['deck_ids'] == [geometry.id]

    existing.refresh_from_db()
    assert existing.kind == StudySet.KIND_CUSTOM
    assert list(existing.decks.values_list('id', flat=True)) == [geometry.id]
    assert existing.tags == []
    assert existing.filenames == []


def test_obsidian_study_set_preview_reports_missing_decks_and_apply_rejects(
    api_client,
    user_factory,
    deck_factory,
):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    operations = deck_factory(user=user, name='Operations', parent=root)
    api_client.force_login(user)

    payload = _study_set_payload(
        links=[
            {'link_text': 'Operations', 'obsidian_path': 'Operations.md'},
            {'link_text': 'Geometry', 'obsidian_path': 'Geometry.md'},
        ]
    )
    preview_response = _post_json(api_client, '/api/v1/obsidian/study-set/preview', payload)

    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview['has_errors'] is True
    assert preview['summary'] == {'deck_count': 1, 'missing_count': 1}
    assert preview['decks'][0]['id'] == operations.id
    assert preview['missing'] == [
        {
            'link_text': 'Geometry',
            'obsidian_path': 'Geometry.md',
            'target_deck_path': 'STEM/Geometry',
        }
    ]

    apply_response = _post_json(api_client, '/api/v1/obsidian/study-set/apply', payload)
    assert apply_response.status_code == 400
    assert apply_response.json()['error'] == 'all referenced decks must exist before applying'
    assert StudySet.objects.filter(user=user, name='Math').exists() is False


def test_obsidian_study_set_rejects_non_markdown_or_anchored_links(api_client, user_factory, deck_factory):
    user = user_factory()
    deck_factory(user=user, name='STEM', parent=None)
    api_client.force_login(user)

    for obsidian_path in ['Operations.pdf', 'Operations.md#Heading', 'Operations.md^block']:
        response = _post_json(
            api_client,
            '/api/v1/obsidian/study-set/preview',
            _study_set_payload(links=[{'link_text': 'Operations', 'obsidian_path': obsidian_path}]),
        )
        assert response.status_code == 400
