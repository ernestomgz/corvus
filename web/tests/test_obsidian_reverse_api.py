import json

import pytest

pytestmark = pytest.mark.django_db


def _preview_payload(*, source_path: str, content: str, root_deck_path: str, source_hash: str) -> dict:
    return {
        'source_path': source_path,
        'content': content,
        'root_deck_path': root_deck_path,
        'source_hash': source_hash,
    }


def test_obsidian_reverse_card_ids_roundtrip(api_client, user_factory, deck_factory):
    user = user_factory()
    deck_factory(user=user, name='STEM', parent=None)
    api_client.force_login(user)

    first_preview = api_client.post(
        '/api/v1/obsidian/preview',
        data=_preview_payload(
            source_path='Languages/capital.md',
            content='## Capital of France\n#card-reverse\nParis',
            root_deck_path='STEM',
            source_hash='hash-reverse-1',
        ),
    )
    assert first_preview.status_code == 201
    first_session = first_preview.json()

    first_apply = api_client.post(
        f"/api/v1/obsidian/preview/{first_session['session_id']}/apply",
        data=json.dumps(
            {
                'source_hash': 'hash-reverse-1',
                'decisions': [
                    {'index': card['index'], 'action': 'apply'}
                    for card in first_session['cards']
                ],
            }
        ),
        content_type='application/json',
    )
    assert first_apply.status_code == 200
    apply_cards = sorted(first_apply.json()['cards'], key=lambda item: item['index'])
    assert len(apply_cards) == 2
    forward_id = apply_cards[0]['import_id']
    reverse_id = apply_cards[1]['import_id']

    second_preview = api_client.post(
        '/api/v1/obsidian/preview',
        data=_preview_payload(
            source_path='Languages/capital.md',
            content=f'## Capital of France\n#card-reverse id:{forward_id}|{reverse_id}\nParis',
            root_deck_path='STEM',
            source_hash='hash-reverse-2',
        ),
    )
    assert second_preview.status_code == 201
    second_cards = sorted(second_preview.json()['cards'], key=lambda item: item['index'])
    assert len(second_cards) == 2
    assert all(card['existing'] for card in second_cards)
    assert all(not card['will_write_back_id'] for card in second_cards)
