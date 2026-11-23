import json

import pytest

from core.models import Card, NoteSource, StudySet
from core.services.review import StudyScope, _scoped_states
from import_md.services import process_markdown_archive

from .test_import_markdown import _build_zip

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


def test_obsidian_study_set_preview_resolves_linked_notes_to_imported_source_paths(
    api_client,
    user_factory,
    deck_factory,
    card_factory,
):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    science = deck_factory(user=user, name='Science', parent=root)
    algebra = deck_factory(user=user, name='Algebra', parent=root)
    card_factory(user=user, deck=science, source_path='notes/Science/math.md')
    card_factory(user=user, deck=science, source_path='notes/Science/math.md')
    card_factory(user=user, deck=algebra, source_path='Algebra/Polynomials.md')
    api_client.force_login(user)

    response = _post_json(
        api_client,
        '/api/v1/obsidian/study-set/preview',
        _study_set_payload(
            links=[
                {'link_text': 'math', 'obsidian_path': 'notes/Science/math.md'},
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
    assert data['summary'] == {'source_path_count': 2, 'card_count': 3, 'missing_count': 0}
    assert data['source_paths'] == [
        {
            'obsidian_path': 'notes/Science/math.md',
            'source_path': 'notes/Science/math.md',
            'link_text': 'math',
            'card_count': 2,
            'note_type': 'questions',
            'deck_paths': ['STEM/Science'],
        },
        {
            'obsidian_path': 'Algebra/Polynomials.md',
            'source_path': 'Algebra/Polynomials.md',
            'link_text': 'Algebra/Polynomials',
            'card_count': 1,
            'note_type': 'questions',
            'deck_paths': ['STEM/Algebra'],
        },
    ]


def test_obsidian_study_set_preview_ignores_matching_source_paths_outside_root_deck(
    api_client,
    user_factory,
    deck_factory,
    card_factory,
):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    other_root = deck_factory(user=user, name='Other', parent=None)
    card_factory(user=user, deck=other_root, source_path='Operations.md')
    api_client.force_login(user)

    response = _post_json(api_client, '/api/v1/obsidian/study-set/preview', _study_set_payload())

    assert response.status_code == 200
    data = response.json()
    assert data['has_errors'] is True
    assert data['summary'] == {'source_path_count': 0, 'card_count': 0, 'missing_count': 2}


def test_obsidian_study_set_apply_creates_custom_preset_with_source_paths(
    api_client,
    user_factory,
    deck_factory,
    card_factory,
):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    operations_deck = deck_factory(user=user, name='Operations Deck', parent=root)
    calculus_deck = deck_factory(user=user, name='Calculus Deck', parent=root)
    card_factory(user=user, deck=operations_deck, source_path='Operations.md')
    card_factory(user=user, deck=calculus_deck, source_path='Calculus.md')
    api_client.force_login(user)

    response = _post_json(api_client, '/api/v1/obsidian/study-set/apply', _study_set_payload())

    assert response.status_code == 201
    data = response.json()
    assert data['status'] == 'applied'
    assert data['action'] == 'created'
    assert data['study_set']['name'] == 'Math'
    assert data['study_set']['kind'] == StudySet.KIND_CUSTOM
    assert data['study_set']['deck_ids'] == []
    assert data['study_set']['source_paths'] == ['Operations.md', 'Calculus.md']
    assert data['study_set']['source_root_deck_id'] == root.id
    assert data['study_set']['source_root_deck_path'] == 'STEM'

    study_set = StudySet.objects.get(user=user, name='Math')
    assert study_set.kind == StudySet.KIND_CUSTOM
    assert study_set.source_root_deck == root
    assert list(study_set.decks.all()) == []
    assert study_set.tags == []
    assert study_set.source_paths == ['Operations.md', 'Calculus.md']


def test_obsidian_study_set_apply_accepts_main_and_question_source_paths(
    api_client,
    user_factory,
    deck_factory,
):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    source_paths = ['Math/Function.md', 'Math/Questions Function.md']
    paths = [*source_paths, 'Math/Other.md']
    upload = _build_zip(dict.fromkeys(paths, 'Question\n#card\nAnswer'))
    process_markdown_archive(user=user, deck=root, uploaded_file=upload)
    api_client.force_login(user)

    response = _post_json(
        api_client,
        '/api/v1/obsidian/study-set/apply',
        _study_set_payload(
            name='Test preset',
            links=[
                {'link_text': 'Function', 'obsidian_path': source_paths[0]},
                {'link_text': 'Questions Function', 'obsidian_path': source_paths[1]},
            ],
        ),
    )

    assert response.status_code == 201
    assert response.json()['study_set']['source_paths'] == source_paths
    study_set = StudySet.objects.get(user=user, name='Test preset')
    assert study_set.source_paths == source_paths

    note = Card.objects.get(user=user, source_path=source_paths[0])
    assert set(_scoped_states(user, deck=note.deck).values_list('card__source_path', flat=True)) == {source_paths[0]}
    assert set(_scoped_states(user, deck=note.deck.parent).values_list('card__source_path', flat=True)) == set(paths)
    scope = StudyScope.from_study_set(study_set)
    assert set(_scoped_states(user, scope=scope).values_list('card__source_path', flat=True)) == set(source_paths)


def test_obsidian_study_set_apply_updates_existing_preset_by_name(
    api_client,
    user_factory,
    deck_factory,
    card_factory,
):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    old_deck = deck_factory(user=user, name='Old', parent=root)
    geometry_deck = deck_factory(user=user, name='Geometry Deck', parent=root)
    card_factory(user=user, deck=geometry_deck, source_path='Geometry.md')
    existing = StudySet.objects.create(
        user=user,
        name='Math',
        kind=StudySet.KIND_CUSTOM,
        tags=['old-tag'],
        source_paths=['old.md'],
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
    assert data['study_set']['deck_ids'] == []
    assert data['study_set']['source_paths'] == ['Geometry.md']
    assert data['study_set']['source_root_deck_id'] == root.id

    existing.refresh_from_db()
    assert existing.kind == StudySet.KIND_CUSTOM
    assert existing.source_root_deck == root
    assert list(existing.decks.all()) == []
    assert existing.tags == []
    assert existing.source_paths == ['Geometry.md']


def test_obsidian_study_set_update_replaces_sources_even_when_some_links_are_missing(
    api_client,
    user_factory,
    deck_factory,
    card_factory,
):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    operations_deck = deck_factory(user=user, name='Operations Deck', parent=root)
    card_factory(user=user, deck=operations_deck, source_path='Operations.md')
    existing = StudySet.objects.create(
        user=user,
        name='Math',
        kind=StudySet.KIND_CUSTOM,
        source_paths=['Calculus.md'],
        source_root_deck=root,
    )
    api_client.force_login(user)

    response = _post_json(
        api_client,
        '/api/v1/obsidian/study-set/apply',
        _study_set_payload(
            links=[
                {'link_text': 'Operations', 'obsidian_path': 'Operations.md'},
                {'link_text': 'Geometry', 'obsidian_path': 'Geometry.md'},
            ]
        ),
    )

    assert response.status_code == 200
    assert response.json()['action'] == 'updated'
    existing.refresh_from_db()
    assert existing.source_paths == ['Operations.md']


def test_obsidian_study_set_apply_allows_partial_missing_notes(
    api_client,
    user_factory,
    deck_factory,
    card_factory,
):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    operations_deck = deck_factory(user=user, name='Operations Deck', parent=root)
    card_factory(user=user, deck=operations_deck, source_path='Operations.md')
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
    assert preview['has_errors'] is False
    assert preview['warnings'] == ['Some referenced notes have not been imported into Corvus.']
    assert preview['summary'] == {'source_path_count': 1, 'card_count': 1, 'missing_count': 1}
    assert preview['source_paths'][0]['source_path'] == 'Operations.md'
    assert preview['missing'] == [
        {
            'link_text': 'Geometry',
            'obsidian_path': 'Geometry.md',
            'source_path': 'Geometry.md',
            'note_type': 'questions',
        }
    ]

    apply_response = _post_json(api_client, '/api/v1/obsidian/study-set/apply', payload)
    assert apply_response.status_code == 201
    data = apply_response.json()
    assert data['action'] == 'created'
    assert data['study_set']['source_paths'] == ['Operations.md']
    assert StudySet.objects.get(user=user, name='Math').source_paths == ['Operations.md']


def test_obsidian_study_set_apply_rejects_when_no_linked_notes_are_imported(
    api_client,
    user_factory,
    deck_factory,
):
    user = user_factory()
    deck_factory(user=user, name='STEM', parent=None)
    api_client.force_login(user)

    payload = _study_set_payload(links=[{'link_text': 'Geometry', 'obsidian_path': 'Geometry.md'}])
    preview_response = _post_json(api_client, '/api/v1/obsidian/study-set/preview', payload)
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview['has_errors'] is True
    assert preview['errors'] == ['No imported cards were found for the referenced notes.']
    assert preview['warnings'] == ['Some referenced notes have not been imported into Corvus.']

    apply_response = _post_json(api_client, '/api/v1/obsidian/study-set/apply', payload)
    assert apply_response.status_code == 400
    assert apply_response.json()['error'] == 'At least one source note must have cards in Corvus before saving the preset.'
    assert not StudySet.objects.filter(user=user, name='Math').exists()


def test_obsidian_preset_preview_and_apply_preserve_explicit_note_types(api_client, deck_factory, card_factory):
    root = deck_factory(name='STEM')
    practice = deck_factory(user=root.user, name='Practice', parent=root)
    card_factory(user=root.user, deck=practice, source_path='BJT.md')
    card_factory(user=root.user, deck=practice, source_path='Exercises.md')
    card_factory(user=root.user, deck=practice, source_path='Exam.md')
    NoteSource.objects.create(
        user=root.user, source_root_deck=root, source_path='Exam.md', note_type='exam-practice',
        type_priority=NoteSource.EXPLICIT,
    )
    api_client.force_login(root.user)
    payload = _study_set_payload(name='BJT', links=[
        {'obsidian_path': 'BJT.md'},
        {'obsidian_path': 'Exercises.md', 'note_type': 'exercises'},
        {'obsidian_path': 'Exam.md', 'note_type': 'questions'},
        {'obsidian_path': 'More exercises.md', 'note_type': 'exercises'},
    ])
    preview = _post_json(api_client, '/api/v1/obsidian/study-set/preview', payload)
    assert preview.status_code == 200
    assert {row['source_path']: row['note_type'] for row in preview.json()['source_paths']} == {
        'BJT.md': 'questions', 'Exercises.md': 'exercises', 'Exam.md': 'exam-practice',
    }
    assert preview.json()['missing'][0]['note_type'] == 'exercises'
    assert NoteSource.objects.filter(user=root.user).count() == 1

    applied = _post_json(api_client, '/api/v1/obsidian/study-set/apply', payload)
    assert applied.status_code == 201
    assert applied.json()['study_set']['source_paths'] == ['BJT.md', 'Exercises.md', 'Exam.md']
    assert dict(NoteSource.objects.values_list('source_path', 'note_type')) == {
        'BJT.md': 'questions', 'Exercises.md': 'exercises', 'Exam.md': 'exam-practice', 'More exercises.md': 'exercises',
    }


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
