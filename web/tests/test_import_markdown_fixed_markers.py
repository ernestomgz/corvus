import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Card
from import_md.services import process_markdown_archive

pytestmark = pytest.mark.django_db


def _build_zip(contents: dict[str, str]) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        for path, text in contents.items():
            zf.writestr(path, text)
    buffer.seek(0)
    return SimpleUploadedFile('cards.zip', buffer.read(), content_type='application/zip')


def _push_markdown(user, deck, markdown: str, *, path: str = 'note.md'):
    archive = _build_zip({path: markdown})
    return process_markdown_archive(user=user, deck=deck, uploaded_file=archive)


def _cards_by_import_id(user) -> dict[str, Card]:
    return {
        card.import_id: card
        for card in Card.objects.filter(user=user).order_by('import_id')
    }


def _assert_cards(user, expected: dict[str, tuple[str, str]]) -> None:
    cards = _cards_by_import_id(user)
    assert set(cards) == set(expected)
    for import_id, (front_md, back_md) in expected.items():
        assert cards[import_id].front_md == front_md
        assert cards[import_id].back_md == back_md


@pytest.mark.parametrize(
    ('markdown', 'created_count', 'expected_cards'),
    [
        pytest.param(
            'Short front\n'
            '#card id:a100\n'
            'Short back\n'
            '\n'
            'Outside the card',
            1,
            {'a100': ('Short front', 'Short back')},
            id='card',
        ),
        pytest.param(
            'Forward front\n'
            '#card-reverse id:a200|a201\n'
            'Forward back\n'
            '\n'
            'Outside the card',
            2,
            {
                'a200': ('Forward front', 'Forward back'),
                'a201': ('Forward back', 'Forward front'),
            },
            id='card-reverse',
        ),
        pytest.param(
            'Long front\n'
            '#long-card id:a300\n'
            'Long back line 1\n'
            '\n'
            'Long back line 2\n'
            '\n'
            '\n'
            'Outside the card',
            1,
            {'a300': ('Long front', 'Long back line 1\n\nLong back line 2')},
            id='long-card',
        ),
        pytest.param(
            'Long reverse front\n'
            '#long-card-reverse id:a400|a401\n'
            'Long reverse back line 1\n'
            '\n'
            'Long reverse back line 2\n'
            '\n'
            '\n'
            'Outside the card',
            2,
            {
                'a400': ('Long reverse front', 'Long reverse back line 1\n\nLong reverse back line 2'),
                'a401': ('Long reverse back line 1\n\nLong reverse back line 2', 'Long reverse front'),
            },
            id='long-card-reverse',
        ),
    ],
)
def test_fixed_markers_create_expected_cards(user_factory, deck_factory, markdown, created_count, expected_cards):
    user = user_factory()
    deck = deck_factory(user=user)

    record = _push_markdown(user, deck, markdown)

    assert record.summary['created'] == created_count
    assert record.summary['updated'] == 0
    _assert_cards(user, expected_cards)


@pytest.mark.parametrize(
    ('initial_markdown', 'updated_markdown', 'updated_count', 'expected_cards'),
    [
        pytest.param(
            'Original front\n#card id:b100\nOriginal back',
            'Updated front\n#card id:b100\nUpdated back',
            1,
            {'b100': ('Updated front', 'Updated back')},
            id='card',
        ),
        pytest.param(
            'Original front\n#card-reverse id:b200|b201\nOriginal back',
            'Updated front\n#card-reverse id:b200|b201\nUpdated back',
            2,
            {
                'b200': ('Updated front', 'Updated back'),
                'b201': ('Updated back', 'Updated front'),
            },
            id='card-reverse',
        ),
        pytest.param(
            'Original front\n'
            '#long-card id:b300\n'
            'Original back line 1\n'
            '\n'
            'Original back line 2\n'
            '\n'
            '\n',
            'Updated front\n'
            '#long-card id:b300\n'
            'Updated back line 1\n'
            '\n'
            'Updated back line 2\n'
            '\n'
            '\n'
            'Outside the card',
            1,
            {'b300': ('Updated front', 'Updated back line 1\n\nUpdated back line 2')},
            id='long-card',
        ),
        pytest.param(
            'Original front\n'
            '#long-card-reverse id:b400|b401\n'
            'Original back line 1\n'
            '\n'
            'Original back line 2\n'
            '\n'
            '\n',
            'Updated front\n'
            '#long-card-reverse id:b400|b401\n'
            'Updated back line 1\n'
            '\n'
            'Updated back line 2\n'
            '\n'
            '\n'
            'Outside the card',
            2,
            {
                'b400': ('Updated front', 'Updated back line 1\n\nUpdated back line 2'),
                'b401': ('Updated back line 1\n\nUpdated back line 2', 'Updated front'),
            },
            id='long-card-reverse',
        ),
    ],
)
def test_fixed_markers_update_existing_cards_by_import_id(
    user_factory,
    deck_factory,
    initial_markdown,
    updated_markdown,
    updated_count,
    expected_cards,
):
    user = user_factory()
    deck = deck_factory(user=user)

    initial_record = _push_markdown(user, deck, initial_markdown)
    update_record = _push_markdown(user, deck, updated_markdown)

    assert initial_record.summary['created'] == len(expected_cards)
    assert update_record.summary['created'] == 0
    assert update_record.summary['updated'] == updated_count
    _assert_cards(user, expected_cards)


@pytest.mark.parametrize(
    ('initial_markdown', 'updated_markdown', 'created_count', 'updated_count', 'expected_cards'),
    [
        pytest.param(
            'Original front\n#card id:c100\nOriginal back',
            'Updated long front\n'
            '#long-card id:c100\n'
            'Updated long back line 1\n'
            '\n'
            'Updated long back line 2\n'
            '\n'
            '\n'
            'Outside the card',
            0,
            1,
            {'c100': ('Updated long front', 'Updated long back line 1\n\nUpdated long back line 2')},
            id='card-to-long-card',
        ),
        pytest.param(
            'Original front\n#card id:c200\nOriginal back',
            'Updated reverse front\n#card-reverse id:c200|c201\nUpdated reverse back',
            1,
            1,
            {
                'c200': ('Updated reverse front', 'Updated reverse back'),
                'c201': ('Updated reverse back', 'Updated reverse front'),
            },
            id='card-to-card-reverse',
        ),
        pytest.param(
            'Original front\n#card id:c300\nOriginal back',
            'Updated long reverse front\n'
            '#long-card-reverse id:c300|c301\n'
            'Updated long reverse back line 1\n'
            '\n'
            'Updated long reverse back line 2\n'
            '\n'
            '\n'
            'Outside the card',
            1,
            1,
            {
                'c300': (
                    'Updated long reverse front',
                    'Updated long reverse back line 1\n\nUpdated long reverse back line 2',
                ),
                'c301': (
                    'Updated long reverse back line 1\n\nUpdated long reverse back line 2',
                    'Updated long reverse front',
                ),
            },
            id='card-to-long-card-reverse',
        ),
    ],
)
def test_fixed_markers_can_change_type_when_import_id_is_kept(
    user_factory,
    deck_factory,
    initial_markdown,
    updated_markdown,
    created_count,
    updated_count,
    expected_cards,
):
    user = user_factory()
    deck = deck_factory(user=user)

    initial_record = _push_markdown(user, deck, initial_markdown)
    update_record = _push_markdown(user, deck, updated_markdown)

    assert initial_record.summary['created'] == 1
    assert update_record.summary['created'] == created_count
    assert update_record.summary['updated'] == updated_count
    _assert_cards(user, expected_cards)


@pytest.mark.parametrize(
    ('first_markdown', 'second_markdown', 'expected_fronts', 'expected_backs'),
    [
        pytest.param(
            'Repeated front\n#card\nFirst back',
            'Repeated front\n#card\nSecond back',
            ['Repeated front', 'Repeated front'],
            ['First back', 'Second back'],
            id='same-front',
        ),
        pytest.param(
            'First front\n#card\nRepeated back',
            'Second front\n#card\nRepeated back',
            ['First front', 'Second front'],
            ['Repeated back', 'Repeated back'],
            id='same-back',
        ),
    ],
)
def test_separate_pushes_without_import_id_create_distinct_cards_for_repeated_content(
    user_factory,
    deck_factory,
    first_markdown,
    second_markdown,
    expected_fronts,
    expected_backs,
):
    user = user_factory()
    deck = deck_factory(user=user)

    first_record = _push_markdown(user, deck, first_markdown, path='same-note.md')
    second_record = _push_markdown(user, deck, second_markdown, path='same-note.md')

    cards = list(Card.objects.filter(user=user).order_by('created_at'))
    assert first_record.summary['created'] == 1
    assert second_record.summary['created'] == 1
    assert second_record.summary['updated'] == 0
    assert len(cards) == 2
    assert [card.front_md for card in cards] == expected_fronts
    assert [card.back_md for card in cards] == expected_backs
    assert cards[0].import_id != cards[1].import_id
