import json

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Card, Deck, ImportSession, NoteSource

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


def test_obsidian_sync_applies_explicit_note_type_even_when_cards_are_unchanged(api_client, deck_factory, card_factory):
    root = deck_factory(name='STEM')
    note_deck = deck_factory(user=root.user, parent=root, name='Exam.md')
    card = card_factory(
        user=root.user, deck=note_deck, source_path='Exam.md', front_md='Question', back_md='Answer', import_id='a1',
    )
    state_id = card.scheduling_state.pk
    api_client.force_login(root.user)
    payload = _preview_payload(
        source_path='Exam.md', root_deck_path='STEM', source_hash='exam-hash',
        content='---\nnote-type: Exam practice\n---\nQuestion\n#card id:a1\nAnswer',
    )
    audit = api_client.post('/api/v1/obsidian/audit-note', data=payload)
    assert audit.status_code == 200
    assert audit.json()['note_type_change'] == 'exam-practice'
    assert audit.json()['updates'] == 0
    assert not NoteSource.objects.exists()

    preview = api_client.post('/api/v1/obsidian/preview', data=payload)
    assert preview.status_code == 201
    session = preview.json()
    assert session['note_type_change'] == 'exam-practice'
    assert session['cards'][0]['unchanged'] is True
    assert not NoteSource.objects.exists()

    apply = api_client.post(
        f"/api/v1/obsidian/preview/{session['session_id']}/apply",
        data=json.dumps({'source_hash': 'exam-hash', 'decisions': [{'index': 0, 'action': 'skip'}]}),
        content_type='application/json',
    )
    assert apply.status_code == 200
    source = NoteSource.objects.get(user=root.user, source_root_deck=root, source_path='Exam.md')
    assert (source.note_type, source.type_priority) == ('exam-practice', NoteSource.EXPLICIT)
    assert card.scheduling_state.pk == state_id
    assert 'note_type_change' not in api_client.post('/api/v1/obsidian/audit-note', data=payload).json()


def test_obsidian_vault_audit_reports_changes_without_writing(
    api_client, user_factory, deck_factory, card_factory, settings,
):
    root = deck_factory(name='STEM')
    user = root.user
    note_deck = deck_factory(user=user, parent=root, name='Circuit.md')
    changed = card_factory(user=user, deck=root, import_id='ab1', front_md='Old', back_md='Answer')
    card_factory(
        user=user, deck=note_deck, import_id='ab2', front_md='Same', back_md='Answer', source_path='Circuit.md',
    )
    card_factory(user=user_factory(), import_id='ab3')
    payload = _preview_payload(
        source_path='Circuit.md', root_deck_path='STEM', source_hash='audit-hash',
        content=(
            'Changed\n#card id:ab1\n![[diagram.png]]\n\n'
            'Same\n#card id:ab2\nAnswer\n\n'
            'Missing\n#card id:ab3\nAnswer\n\n'
            'New\n#card\nAnswer'
        ),
    )
    payload['attachment_manifest'] = json.dumps([{'field': 'image', 'path': 'diagram.png'}])
    payload['image'] = SimpleUploadedFile('diagram.png', b'image bytes')

    assert api_client.post('/api/v1/obsidian/audit-note').status_code == 401
    api_client.force_login(user)
    response = api_client.post('/api/v1/obsidian/audit-note', data=payload)

    assert response.status_code == 200
    assert response.json() == {
        'identified': 3, 'found': 2, 'updates': 1, 'moves': 1, 'unchanged': 1, 'without_ids': 1, 'errors': [],
        'identities': [{'kind': 'marker', 'value': value} for value in ('ab1', 'ab2', 'ab3')],
    }
    changed.refresh_from_db()
    assert changed.deck_id == root.id
    assert changed.front_md == 'Old'
    assert Card.objects.filter(user=user).count() == 2
    assert Deck.objects.filter(user=user).count() == 2
    assert not ImportSession.objects.filter(user=user).exists()
    assert not list(settings.MEDIA_ROOT.iterdir())


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
    assert data['planned_decks'] == ['STEM/Math', 'STEM/Math/Functions', 'STEM/Math/Functions/exponential.md']
    assert data['has_errors'] is False
    assert len(data['cards']) == 1

    card = data['cards'][0]
    assert card['marker_line'] == 3
    assert card['marker_kind'] == 'card'
    assert card['target_deck_path'] == 'STEM/Math/Functions/exponential.md'
    assert card['will_write_back_id'] is True
    assert settings.MEDIA_URL in card['back_md']

    detail_response = api_client.get(f"/api/v1/obsidian/preview/{data['session_id']}")
    assert detail_response.status_code == 200
    detail_data = detail_response.json()
    assert detail_data['session_id'] == data['session_id']
    assert detail_data['cards'][0]['target_deck_path'] == 'STEM/Math/Functions/exponential.md'


def test_obsidian_tikz_attachment_source_change_is_visible_to_audit(api_client, deck_factory, settings):
    root = deck_factory(name='STEM')
    api_client.force_login(root.user)

    def payload(source_digest, image=b'same exported PNG bytes'):
        filename = f'tikz-{source_digest}.png'
        path = f'__corvus_tikz__/{filename}'
        data = _preview_payload(
            source_path='Circuits/Transistor.md', root_deck_path='STEM', source_hash=source_digest,
            content=f'Circuit\n#card id:a1\n![TikZ diagram]({path})\nExplanation after the diagram',
        )
        data['attachment_manifest'] = json.dumps([{'field': 'attachment_0', 'path': path}])
        data['attachment_0'] = SimpleUploadedFile(filename, image, content_type='image/png')
        return data

    original_digest = 'a' * 64
    edited_digest = 'b' * 64
    preview = api_client.post('/api/v1/obsidian/preview', data=payload(original_digest))
    assert preview.status_code == 201
    session_id = preview.json()['session_id']
    applied = api_client.post(
        f'/api/v1/obsidian/preview/{session_id}/apply',
        data=json.dumps({'source_hash': original_digest, 'decisions': [{'index': 0, 'action': 'apply'}]}),
        content_type='application/json',
    )
    assert applied.status_code == 200
    card = Card.objects.get(user=root.user, import_id='a1')
    assert card.back_md.startswith(f'![TikZ diagram]({settings.MEDIA_URL}')
    assert card.back_md.endswith('Explanation after the diagram')
    assert card.media[0]['name'] == f'tikz-{original_digest}.png'
    media_dir = settings.MEDIA_ROOT / str(root.user_id)
    existing_files = set(media_dir.iterdir())

    unchanged = api_client.post('/api/v1/obsidian/audit-note', data=payload(original_digest))
    assert unchanged.json()['unchanged'] == 1

    for edited in [payload(edited_digest), payload(original_digest, b'different image bytes')]:
        audit = api_client.post('/api/v1/obsidian/audit-note', data=edited)
        assert audit.status_code == 200
        assert audit.json()['updates'] == 1
        assert audit.json()['moves'] == 0

    assert set(media_dir.iterdir()) == existing_files
    card.refresh_from_db()
    assert card.media[0]['name'] == f'tikz-{original_digest}.png'


def test_obsidian_reverse_cards_reuse_ids_and_preview_matches_audit(api_client, deck_factory):
    root = deck_factory(name='STEM')
    api_client.force_login(root.user)
    prompt = '💡 What does `#card id:ca` mean? ![Circuit #card example](</Images/final%20image.png> "Layout")'
    line = f'{prompt} #card-reverse'
    content = f'---\ntitle: Circuit\n---\n{line}\nThe answer.'

    def post_note(endpoint, markdown):
        data = _preview_payload(source_path='Circuits/Circuit.md', root_deck_path='STEM', source_hash='capture', content=markdown)
        data['attachment_manifest'] = json.dumps([{'field': 'image', 'path': 'Images/final image.png'}])
        data['image'] = SimpleUploadedFile('final image.png', b'image bytes', content_type='image/png')
        return api_client.post(f'/api/v1/obsidian/{endpoint}', data=data)

    def apply(preview):
        return api_client.post(f"/api/v1/obsidian/preview/{preview['session_id']}/apply",
                               data=json.dumps({'source_hash': 'capture'}), content_type='application/json')

    audit = post_note('audit-note', content).json()
    response = post_note('preview', content)
    assert response.status_code == 201
    preview = response.json()
    forward, reverse = preview['cards']
    assert audit['without_ids'] == len(preview['cards']) == 2
    assert not audit['errors'] and not preview['has_errors']
    assert forward['marker_line'] == reverse['marker_line'] == 4
    assert forward['marker_column'] == reverse['marker_column'] == len(f'{prompt} '.encode('utf-16-le')) // 2
    assert '`#card id:ca`' in forward['front_md']
    assert forward['front_md'].endswith(' "Layout")')
    assert forward['front_md'] == reverse['back_md']
    applied = apply(preview)
    assert applied.status_code == 200
    assert applied.json()['summary']['created'] == 2

    cards = {card.import_id: card for card in Card.objects.filter(user=root.user)}
    stable = {import_id: card.pk for import_id, card in cards.items()}
    state = cards[forward['import_id']].scheduling_state
    state.interval_days = 12
    state.save(update_fields=['interval_days'])
    synced = content.replace(line, f"{line} id:{forward['import_id']}|{reverse['import_id']}").replace('The answer.', 'Updated answer.')
    audit = post_note('audit-note', synced).json()
    again = post_note('preview', synced).json()
    assert audit['found'] == audit['updates'] == again['summary']['updates'] == 2
    assert apply(again).json()['summary']['updated'] == 2
    assert {card.import_id: card.pk for card in Card.objects.filter(user=root.user)} == stable
    state.refresh_from_db()
    assert state.interval_days == 12


def test_obsidian_missing_answer_blocks_apply_and_audit_reports_location(api_client, deck_factory):
    root = deck_factory(name='STEM')
    api_client.force_login(root.user)
    payload = _preview_payload(
        source_path='Notes/Circuit.md', root_deck_path='STEM', source_hash='incomplete',
        content='# Topic\n\nAsk a question. #card',
    )
    preview = api_client.post('/api/v1/obsidian/preview', data=payload).json()
    assert preview['has_errors']
    assert preview['cards'][0]['errors'] == ['Missing answer.']
    audit = api_client.post('/api/v1/obsidian/audit-note', data=payload).json()
    assert audit['error_locations'] == [{'marker_line': 3, 'errors': ['Missing answer.']}]
    response = api_client.post(
        f"/api/v1/obsidian/preview/{preview['session_id']}/apply",
        data=json.dumps({'source_hash': 'incomplete', 'decisions': [{'index': 0, 'action': 'skip'}]}),
        content_type='application/json',
    )
    assert response.status_code == 400
    assert 'Notes/Circuit.md line 3: Missing answer.' in response.json()['error']
    assert not Card.objects.filter(user=root.user).exists()


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
    assert card.deck == Deck.objects.get(user=user, parent=functions, name='exponential.md')
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
    assert preview_data['planned_decks'] == ['STEM/Science', 'STEM/Science/topic.md']
    assert preview_card['existing'] is True
    assert preview_card['has_changes'] is True
    assert preview_card['unchanged'] is False
    assert preview_card['existing_deck_path'] == 'STEM/Math'
    assert preview_card['target_deck_path'] == 'STEM/Science/topic.md'
    assert preview_card['metadata_changes']['deck'] == {
        'from': 'STEM/Math',
        'to': 'STEM/Science/topic.md',
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
    assert apply_data['summary']['decks_created'] == 2

    science = Deck.objects.get(user=user, parent=root, name='Science')
    card.refresh_from_db()
    assert card.deck == Deck.objects.get(user=user, parent=science, name='topic.md')
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
