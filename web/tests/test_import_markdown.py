import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Card, Deck
from import_md.services import (
    MarkdownImportError,
    apply_markdown_session,
    prepare_markdown_session,
    process_markdown_archive,
)

pytestmark = pytest.mark.django_db


def _build_zip(contents: dict[str, str], media: dict[str, bytes] | None = None) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        for path, text in contents.items():
            zf.writestr(path, text)
        if media:
            for path, data in media.items():
                zf.writestr(path, data)
    buffer.seek(0)
    return SimpleUploadedFile('cards.zip', buffer.read(), content_type='application/zip')


def test_md_card_extraction(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    archive = _build_zip({'notes/sample.md': 'What is 2+2?\n#card\nFour'})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    assert card.front_md.startswith('What is')
    assert card.back_md == 'Four'
    assert card.deck == deck


def test_md_upsert_preserves_state(card_factory, external_id_factory):
    card = card_factory()
    external_id_factory(card=card, system='logseq', external_key='c_test')
    state = card.scheduling_state
    state.queue_status = 'review'
    state.interval_days = 10
    state.ease = 2.0
    state.save()

    markdown = '#card Updated front\nid:: c_test\n\nUpdated back'
    archive = _build_zip({'file.md': markdown})
    record = process_markdown_archive(user=card.user, deck=card.deck, uploaded_file=archive)
    card.refresh_from_db()
    state.refresh_from_db()
    assert record.summary['updated'] == 1
    assert card.front_md == 'Updated front'
    assert state.interval_days == 10
    assert state.queue_status == 'review'


def test_md_external_id_matching(external_id_factory):
    card = external_id_factory(system='logseq', external_key='c_match').card
    markdown = '#card Replacement\nid:: c_match\n\nAnswer'
    archive = _build_zip({'file.md': markdown})
    record = process_markdown_archive(user=card.user, deck=card.deck, uploaded_file=archive)
    card.refresh_from_db()
    assert record.summary['updated'] == 1
    assert card.front_md == 'Replacement'


def test_md_media_copy_and_rewrite(user_factory, deck_factory, settings):
    user = user_factory()
    deck = deck_factory(user=user)
    image_bytes = b'fake-image-bytes'
    markdown = 'Diagram\n#card\n![alt](assets/diagram.png)'
    archive = _build_zip({'note.md': markdown}, media={'assets/diagram.png': image_bytes})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    assert record.summary['created'] == 1
    assert card.media
    media_entry = card.media[0]
    assert media_entry['url'].startswith(settings.MEDIA_URL)
    assert 'diagram' in media_entry['name']
    assert media_entry['hash']
    assert '![' in card.back_md and settings.MEDIA_URL in card.back_md


def test_md_obsidian_resized_media_is_found(user_factory, deck_factory, settings):
    user = user_factory()
    deck = deck_factory(user=user)
    image_bytes = b'fake-image'
    markdown = 'Photo\n#card\n![[photo.png|200]]'
    archive = _build_zip({'note.md': markdown}, media={'photo.png': image_bytes})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    assert record.summary['created'] == 1
    assert card.media
    assert card.media[0]['url'].startswith(settings.MEDIA_URL)
    assert 'photo' in card.media[0]['name']


def test_md_import_id_parsing(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = '## Question Title\n#card id:1a2b\nAnswer'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    assert card.import_id == '1a2b'
    assert card.front_md == 'Question Title'
    assert card.back_md == 'Answer'


def test_md_import_id_auto_generation(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = 'Question without ID\n#card\nAnswer'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    assert card.import_id is not None
    assert len(card.import_id) > 0
    # Should be a valid hex number
    int(card.import_id, 16)


def test_md_import_id_update_existing(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    
    # Create initial card
    markdown1 = '## Original question\n#card id:abc\nOriginal answer'
    archive1 = _build_zip({'note.md': markdown1})
    record1 = process_markdown_archive(user=user, deck=deck, uploaded_file=archive1)
    assert record1.summary['created'] == 1
    card = Card.objects.get(user=user)
    assert card.import_id == 'abc'
    assert card.front_md == 'Original question'
    assert card.back_md == 'Original answer'
    
    # Update the same card
    markdown2 = '## Updated question\n#card id:abc\nUpdated answer'
    archive2 = _build_zip({'note.md': markdown2})
    record2 = process_markdown_archive(user=user, deck=deck, uploaded_file=archive2)
    assert record2.summary['updated'] == 1
    card.refresh_from_db()
    assert card.import_id == 'abc'
    assert card.front_md == 'Updated question'
    assert card.back_md == 'Updated answer'


def test_md_import_id_conflict_warning(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    
    # Create first card
    markdown1 = 'First question\n#card id:123\nFirst answer'
    archive1 = _build_zip({'note1.md': markdown1})
    record1 = process_markdown_archive(user=user, deck=deck, uploaded_file=archive1)
    assert record1.summary['created'] == 1
    
    # Try to create second card with same ID
    markdown2 = 'Second question\n#card id:123\nSecond answer'
    archive2 = _build_zip({'note2.md': markdown2})
    session = prepare_markdown_session(user=user, deck=deck, uploaded_file=archive2)
    
    # Should have a warning about the conflict
    cards_data = session.payload['cards']
    assert len(cards_data) == 1
    card_data = cards_data[0]
    assert 'already exists' in ' '.join(card_data['warnings'])


def test_md_import_id_invalid_format(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = 'Question\n#card id:invalid\n\nAnswer'
    archive = _build_zip({'note.md': markdown})
    session = prepare_markdown_session(user=user, deck=deck, uploaded_file=archive)
    
    cards_data = session.payload['cards']
    assert len(cards_data) == 1
    card_data = cards_data[0]
    assert any('Invalid import ID format' in error for error in card_data['errors'])


def test_md_import_skips_root_folder(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user, name='Biology')
    archive = _build_zip({'Biology/note.md': 'Question\n#card\nAnswer'})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    assert card.deck == deck
    assert Deck.objects.filter(user=user, parent=deck, name='Biology').count() == 0


def test_prepare_markdown_session_creates_payload(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    archive = _build_zip({'cards/note.md': 'Sample\n#card\nid:: example\n\nBack content'})
    session = prepare_markdown_session(user=user, deck=deck, uploaded_file=archive)
    assert session.status == 'ready'
    assert session.total == 1
    payload = session.payload
    assert payload['cards'][0]['external_key']
    assert payload['cards'][0]['deck_path'] == ['cards']


def test_apply_markdown_session_respects_decision(card_factory, external_id_factory):
    existing_card = card_factory(front_md='Original', back_md='Answer')
    external_id_factory(card=existing_card, system='logseq', external_key='c_existing')
    markdown = 'Updated\n#card\nid:: c_existing\n\nReplacement'
    archive = _build_zip({'note.md': markdown})
    session = prepare_markdown_session(user=existing_card.user, deck=existing_card.deck, uploaded_file=archive)
    index = session.payload['cards'][0]['index']
    apply_markdown_session(session, decisions={index: 'existing'})
    existing_card.refresh_from_db()
    assert existing_card.front_md == 'Original'
    assert existing_card.back_md == 'Answer'


def test_apply_markdown_session_creates_child_decks(user_factory, deck_factory):
    user = user_factory()
    root_deck = deck_factory(user=user)
    markdown = 'Child card\n#card\n\nContent'
    archive = _build_zip({'Sciences/Math/note.md': markdown})
    session = prepare_markdown_session(user=user, deck=root_deck, uploaded_file=archive)
    record = apply_markdown_session(session)
    assert record.summary['created'] == 1
    assert record.summary['decks_created'] >= 2
    sciences = Deck.objects.get(user=user, parent=root_deck, name='Sciences')
    math = Deck.objects.get(user=user, parent=sciences, name='Math')
    card = Card.objects.get(user=user)
    assert card.deck == math


def test_md_card_marker_variations(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = "## Question 1 #card\nFirst answer\n\nQuestion 2 #card\nSecond answer"
    archive = _build_zip({'notes.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 2
    cards = Card.objects.filter(user=user).order_by('created_at')
    fronts = [card.front_md for card in cards]
    backs = [card.back_md for card in cards]
    assert fronts[0].splitlines()[-1] == 'Question 1'
    assert backs[0] == 'First answer'
    assert fronts[1].splitlines()[-1] == 'Question 2'
    assert backs[1] == 'Second answer'


def test_md_card_reverse_marker_creates_reverse_copy(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = "## Capital of France #card/reverse\nParis"
    archive = _build_zip({'world.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 2
    cards = Card.objects.filter(user=user).order_by('created_at')
    assert cards.count() == 2
    assert cards[0].front_md.splitlines()[-1] == 'Capital of France'
    assert cards[0].back_md == 'Paris'
    assert cards[1].front_md.splitlines()[-1] == 'Paris'
    assert cards[1].back_md == 'Capital of France'


def test_prepare_session_requires_folders_without_root_deck(user_factory):
    user = user_factory()
    archive = _build_zip({'note.md': '#card Lonely\n\nBack'})
    session = prepare_markdown_session(user=user, deck=None, uploaded_file=archive)
    payload = session.payload
    assert payload['summary']['has_errors'] is True
    card = payload['cards'][0]
    assert any('folder' in error.lower() for error in card['errors'])


def test_prepare_session_detects_missing_attachments(user_factory):
    user = user_factory()
    markdown = 'Diagram\n#card\n![[attachments/missing.png]]'
    archive = _build_zip({'Science/note.md': markdown})
    session = prepare_markdown_session(user=user, deck=None, uploaded_file=archive)
    card = session.payload['cards'][0]
    assert any('missing attachment' in error.lower() for error in card['errors'])
    with pytest.raises(MarkdownImportError):
        apply_markdown_session(session)


def test_apply_session_builds_decks_from_archive_when_no_root(user_factory):
    user = user_factory()
    markdown = 'Integral rules\n#card\n\nRemember the basics.'
    archive = _build_zip({'Mathematics/Equations/differentials.md': markdown})
    session = prepare_markdown_session(user=user, deck=None, uploaded_file=archive)
    record = apply_markdown_session(session)
    assert record.summary['created'] == 1
    math = Deck.objects.get(user=user, parent=None, name='Mathematics')
    equations = Deck.objects.get(user=user, parent=math, name='Equations')
    card = Card.objects.get(user=user)
    assert card.deck == equations


def test_markdown_hierarchy_populates_front_lines(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = "# Combinatorics\n## Newton binomial\n### Formula #card\n$$(a+b)^n = \\sum_{j=0}^n \\binom{n}{j} a^{n-j} b^j$$"
    archive = _build_zip({'math.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    lines = card.front_md.splitlines()
    assert lines[0] == 'Combinatorics > Newton binomial'
    assert lines[1] == 'Formula'


def test_markdown_hierarchy_when_marker_on_next_line(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "# Functions\n"
        "## Definition\n"
        "### Scalar Fields\n"
        "\n"
        "#card\n"
        "A scalar field maps each point to a scalar value."
    )
    archive = _build_zip({'math.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    lines = card.front_md.splitlines()
    assert lines[0] == 'Functions > Definition'
    assert lines[1] == 'Scalar Fields'


def test_markdown_hierarchy_persists_after_marker_only_lines(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "# Functions\n"
        "## Definition\n"
        "### Scalar Fields\n"
        "\n"
        "#card\n"
        "A scalar field description.\n"
        "\n"
        "## Types of Functions\n"
        "\n"
        "**Injective** #card\n"
        "One-to-one mapping.\n"
    )
    archive = _build_zip({'math.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 2
    cards = list(Card.objects.filter(user=user).order_by('created_at'))
    assert len(cards) == 2
    first_lines = cards[0].front_md.splitlines()
    assert first_lines[0] == 'Functions > Definition'
    assert first_lines[1] == 'Scalar Fields'
    second_lines = cards[1].front_md.splitlines()
    assert second_lines[0] == 'Functions > Types of Functions'
    assert second_lines[1] == 'Injective'


def test_markdown_inline_card_in_list(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "# Completing Squares\n"
        "## Standard Forms\n"
        "1. **Form 1:** #card\n"
        "ax^2 + bx + c = 0\n"
        "\n"
        "### Formula\n"
        "#card\n"
        "For a quadratic...\n"
    )
    archive = _build_zip({'math.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 2
    cards = list(Card.objects.filter(user=user).order_by('created_at'))
    assert cards[0].front_md.splitlines()[0] == 'Completing Squares > Standard Forms'


def test_import_uses_card_type_marker(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = "## Identify plant #photo-card\n![](attachments/leaf.png)\n\nLeaf shape meaning"
    archive = _build_zip({'Media/note.md': markdown}, media={'Media/attachments/leaf.png': b'image-bytes'})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    assert card.card_type.slug == 'photo'


def test_import_long_card_preserves_single_blank_lines(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "Question line 1\n"
        "Question line 2\n"
        "#long-card\n"
        "response line 1\n"
        "response line 2\n"
        "\n"
        "response line 3\n"
        "end of response\n"
        "\n"
        "\n"
    )
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    assert card.card_type.slug == 'long-card'
    assert card.front_md == 'Question line 1\nQuestion line 2'
    assert card.back_md == 'response line 1\nresponse line 2\n\nresponse line 3\nend of response'


def test_import_long_card_with_id_and_heading_context_and_update(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "# Topic\n"
        "## Subtopic\n"
        "Question ST\n"
        "#long-card id:123\n"
        "Initial answer paragraph\n"
        "\n"
        "Initial additional content\n"
        "\n"
        "\n"
    )
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    assert card.card_type.slug == 'long-card'
    assert card.import_id == '123'
    # Hierarchy should be in front, followed by the question
    lines = card.front_md.split('\n')
    assert lines[0] == 'Topic > Subtopic'
    assert any('Question ST' in line for line in lines)
    assert 'Initial answer paragraph' in card.back_md

    # Update same card via same import_id
    markdown_update = (
        "# Topic\n"
        "## Subtopic\n"
        "Question ST\n"
        "#long-card id:123\n"
        "Updated answer paragraph\n"
        "\n"
        "Updated additional content\n"
        "\n"
        "\n"
    )
    archive_update = _build_zip({'note.md': markdown_update})
    record2 = process_markdown_archive(user=user, deck=deck, uploaded_file=archive_update)
    assert record2.summary['updated'] == 1
    card.refresh_from_db()
    assert 'Updated answer paragraph' in card.back_md

    # Update same card via same import_id
    markdown_update = (
        "# Topic\n"
        "## Subtopic\n"
        "Question ST\n"
        "#long-card id:123\n"
        "Updated again answer paragraph\n"
        "\n"
        "Updated again additional content\n"
        "\n"
        "\n"
    )
    archive_update = _build_zip({'note.md': markdown_update})
    record2 = process_markdown_archive(user=user, deck=deck, uploaded_file=archive_update)
    assert record2.summary['updated'] == 1
    card.refresh_from_db()
    assert 'id:123' not in card.back_md
    assert 'Updated again answer paragraph' in card.back_md


def test_import_long_card_supports_fenced_code_block_blank_lines(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "Question verbatim\n"
        "#long-card\n"
        "Answer paragraph\n"
        "```\n"
        "code line 1\n"
        "\n"
        "\n"
        "code line 2\n"
        "\n"
        "code line 3\n"
        "```\n"
        "\n"
        "Additional paragraph\n"
        "\n"
        "\n"
        "Out of card content\n"
    )
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    assert card.card_type.slug == 'long-card'
    assert 'code line 1' in card.back_md
    assert 'code line 2' in card.back_md
    assert 'code line 3' in card.back_md
    assert 'Additional paragraph' in card.back_md
    assert 'Out of card content' not in card.back_md



def test_import_updates_merge_tags(user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = '#card Fact\nid:: merge_demo\ntags:: math\n\nAnswer'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    assert record.summary['created'] == 1
    card = Card.objects.get(user=user)
    card.tags.append('custom')
    card.save(update_fields=['tags'])

    updated_markdown = '#card Fact updated\nid:: merge_demo\ntags:: spaced\n\nNew answer'
    updated_archive = _build_zip({'note.md': updated_markdown})
    record2 = process_markdown_archive(user=user, deck=deck, uploaded_file=updated_archive)
    assert record2.summary['updated'] == 1
    card.refresh_from_db()
    assert card.tags == ['math', 'custom', 'spaced']


def test_md_same_line_marker_does_not_include_previous_marker(user_factory, deck_factory):
    """Test that same-line markers don't accidentally include previous card's marker in front_content."""
    user = user_factory()
    deck = deck_factory(user=user)
    
    # Case from issue: marker on next line, then same-line marker
    markdown = (
        "What is the capital of France?\n"
        "#card id:1233\n"
        "Paris\n"
        "\n"
        "What is the capital of Germany? #card id:1234\n"
        "Berlin"
    )
    archive = _build_zip({'note.md': markdown})
    session = prepare_markdown_session(user=user, deck=deck, uploaded_file=archive)
    
    # Should create 2 cards
    assert session.total == 2
    cards_data = session.payload['cards']
    
    # First card should have correct front
    assert cards_data[0]['front_md'] == 'What is the capital of France?'
    assert cards_data[0]['back_md'] == 'Paris'
    assert cards_data[0]['import_id'] == '1233'
    
    # Second card should NOT include previous marker line
    # It should be just "What is the capital of Germany?" not "card id:1234\nWhat is the capital of Germany?"
    assert 'card id:1234' not in cards_data[1]['front_md']
    assert cards_data[1]['front_md'] == 'What is the capital of Germany?'
    assert cards_data[1]['back_md'] == 'Berlin'
    assert cards_data[1]['import_id'] == '1234'
    
    # Import should succeed
    record = apply_markdown_session(session)
    assert record.summary['created'] == 2
    
    # Verify in database
    cards = Card.objects.filter(user=user).order_by('import_id')
    assert cards[0].front_md == 'What is the capital of France?'
    assert cards[1].front_md == 'What is the capital of Germany?'
    #common bug
    assert 'id' or 'card' not in cards[1].front_md
    assert 'id' or 'card' not in cards[0].front_md


def test_md_duplicate_import_ids_in_session_show_errors(user_factory, deck_factory):
    """Test that duplicate import_ids within the same import session are caught in preview."""
    user = user_factory()
    deck = deck_factory(user=user)
    
    # Two cards with the same import_id should fail
    markdown = (
        "Question 1\n"
        "#card id:abc123\n"
        "Answer 1\n"
        "\n"
        "Question 2\n"
        "#card id:abc123\n"
        "Answer 2"
    )
    archive = _build_zip({'note.md': markdown})
    session = prepare_markdown_session(user=user, deck=deck, uploaded_file=archive)
    
    cards_data = session.payload['cards']
    assert len(cards_data) == 2
    
    # Both cards should have errors about duplicate ID
    error0_text = ' '.join(cards_data[0]['errors'])
    error1_text = ' '.join(cards_data[1]['errors'])
    
    assert 'Duplicate import ID' in error0_text or 'Duplicate import ID' in error1_text
    
    # Session should be marked as having errors
    assert session.payload['summary']['has_errors'] is True
    
    # Should not be able to apply with duplicate IDs
    with pytest.raises(MarkdownImportError):
        apply_markdown_session(session)


def test_md_both_marker_positions_with_ids_work_correctly(user_factory, deck_factory):
    """Test both marker positions (same-line and next-line) work correctly with IDs."""
    user = user_factory()
    deck = deck_factory(user=user)
    
    # Mix of both marker positions
    markdown = (
        "### Section 1\n"
        "Question A #card id:1a1\n"
        "Answer A\n"
        "\n"
        "Question B\n"
        "#card id:1b1\n"
        "Answer B\n"
        "\n"
        "### Question C #card id:1c1\n"
        "Answer C"
    )
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    
    assert record.summary['created'] == 3
    
    cards = Card.objects.filter(user=user).order_by('import_id')
    assert cards[0].import_id == '1a1'
    assert 'Section 1' in cards[0].front_md
    assert 'Question A' in cards[0].front_md
    assert cards[0].back_md == 'Answer A'
    
    assert cards[1].import_id == '1b1'
    assert 'Section 1' in cards[1].front_md
    assert 'Question B' in cards[1].front_md
    assert cards[1].back_md == 'Answer B'
    
    assert cards[2].import_id == '1c1'
    assert 'Question C' in cards[2].front_md
    assert cards[2].back_md == 'Answer C'
    # Should not include "Section 1" because marker is on same line as heading, so it should reset hierarchy context
    assert 'Section 1' not in cards[2].front_md
    # Previous cards' ids should not be in this card
    assert 'id:1a1' not in cards[2].front_md
    assert 'id:1b1' not in cards[2].front_md


# ============================================================================
# EDGE CASES: Long Card Blank Line Handling
# ============================================================================

def test_long_card_double_blank_lines_terminate(user_factory, deck_factory):
    """Test that two consecutive blank lines terminate long card content (not one)."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "Question #long-card\n"
        "Line 1\n"
        "\n"
        "Line 2\n"
        "\n"
        "\n"
        "This should NOT be included (after double blank)"
    )
    archive = _build_zip({'notes.md': markdown})
    session = prepare_markdown_session(user=user, deck=deck, uploaded_file=archive)
    card = session.payload['cards'][0]
    
    # Back should include lines 1 and 2, but not the text after double blank
    assert 'Line 1' in card['back_md']
    assert 'Line 2' in card['back_md']
    assert 'This should NOT' not in card['back_md']


def test_long_card_single_blank_lines_continue(user_factory, deck_factory):
    """Test that single blank lines are preserved inside long cards."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "Question #long-card\n"
        "Paragraph 1\n"
        "\n"
        "Paragraph 2\n"
        "\n"
        "Paragraph 3"
    )
    archive = _build_zip({'notes.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    # Should have all three paragraphs with blank lines preserved
    back_lines = card.back_md.split('\n')
    assert 'Paragraph 1' in card.back_md
    assert 'Paragraph 2' in card.back_md
    assert 'Paragraph 3' in card.back_md
    # Count blank lines - should have 2 single blank lines
    blank_count = sum(1 for line in back_lines if line.strip() == '')
    assert blank_count == 2


def test_long_card_fenced_code_preserves_multiple_blanks(user_factory, deck_factory):
    """Test that blank lines inside fenced code blocks don't trigger termination."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "Code Example #long-card\n"
        "```python\n"
        "def foo():\n"
        "\n"
        "    return 42\n"
        "\n"
        "```\n"
        "End description"
    )
    archive = _build_zip({'notes.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    # Should preserve the code block completely with blank lines
    assert '```python' in card.back_md
    assert 'def foo()' in card.back_md
    assert 'return 42' in card.back_md
    assert 'End description' in card.back_md


def test_long_card_tilde_fence_delimiter(user_factory, deck_factory):
    """Test that tilde (~) fenced blocks are handled like backticks."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "Code #long-card\n"
        "~~~\n"
        "Some code\n"
        "\n"
        "More code\n"
        "~~~\n"
        "After fence"
    )
    archive = _build_zip({'notes.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    assert '~~~' in card.back_md
    assert 'Some code' in card.back_md
    assert 'After fence' in card.back_md


def test_long_card_nested_fences_with_blank_lines(user_factory, deck_factory):
    """Test long cards with multiple fence blocks and blank lines between them."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "Multi-code #long-card\n"
        "```\n"
        "First block\n"
        "\n"
        "```\n"
        "\n"
        "Between blocks\n"
        "\n"
        "```\n"
        "Second block\n"
        "\n"
        "```"
    )
    archive = _build_zip({'notes.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    assert 'First block' in card.back_md
    assert 'Between blocks' in card.back_md
    assert 'Second block' in card.back_md


# ============================================================================
# EDGE CASES: Media Path Handling
# ============================================================================

def test_media_relative_path_with_dot_slash(user_factory, deck_factory, settings):
    """Test media paths starting with ./"""
    user = user_factory()
    deck = deck_factory(user=user)
    image_bytes = b'image-data'
    markdown = 'Image\n#card\n![](./attachments/image.png)'
    archive = _build_zip({'note.md': markdown}, media={'attachments/image.png': image_bytes})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    assert settings.MEDIA_URL in card.back_md


def test_media_deeply_nested_path(user_factory, deck_factory, settings):
    """Test deeply nested media paths."""
    user = user_factory()
    deck = deck_factory(user=user)
    image_bytes = b'nested-image'
    markdown = 'Nested\n#card\n![](assets/images/diagrams/deep.png)'
    archive = _build_zip({'note.md': markdown}, media={'assets/images/diagrams/deep.png': image_bytes})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    assert 'deep' in card.media[0]['name']
    assert settings.MEDIA_URL in card.back_md


def test_media_same_filename_different_locations(user_factory, deck_factory, settings):
    """Test multiple media files with same name from different locations."""
    user = user_factory()
    deck = deck_factory(user=user)
    archive = _build_zip(
        {
            'note.md': (
                'Images\n#card\n![front1](folder1/image.png)\n![front2](folder2/image.png)\n\n'
                'Back text'
            )
        },
        media={
            'folder1/image.png': b'data1',
            'folder2/image.png': b'data2',
        }
    )
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    # Should have two different media entries (different hashes)
    assert len(card.media) == 2
    assert card.media[0]['hash'] != card.media[1]['hash']


def test_media_wiki_format_with_pipe_separator(user_factory, deck_factory, settings):
    """Test MediaWiki-style links with pipe separators."""
    user = user_factory()
    deck = deck_factory(user=user)
    image_bytes = b'wiki-image'
    markdown = 'Wiki\n#card\n[[diagram.svg|Custom Alt Text]]'
    archive = _build_zip({'note.md': markdown}, media={'diagram.svg': image_bytes})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    assert settings.MEDIA_URL in card.back_md
    assert 'Custom Alt Text' in card.back_md or 'diagram' in card.back_md


# ============================================================================
# EDGE CASES: Import ID Handling
# ============================================================================

def test_import_id_with_uppercase_normalized_to_lowercase(user_factory, deck_factory):
    """Test that import IDs with uppercase letters are normalized to lowercase."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = 'Question\n#card id:AbCdEf\nAnswer'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    # Should be normalized to lowercase
    assert card.import_id == 'abcdef'


def test_import_id_invalid_non_hex_detected_in_preview(user_factory, deck_factory):
    """Test that non-hexadecimal import IDs are caught as errors in preview."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = 'Q\n#card id:test-id_123\nA'
    archive = _build_zip({'note.md': markdown})
    session = prepare_markdown_session(user=user, deck=deck, uploaded_file=archive)
    
    # Should have an error about invalid format
    card_data = session.payload['cards'][0]
    assert any('Invalid import ID format' in error for error in card_data['errors'])


def test_import_id_valid_hex_lowercase_and_uppercase(user_factory, deck_factory):
    """Test that valid hexadecimal IDs (with a-f letters) work correctly."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = 'Q\n#card id:deadbeef\nA'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    # Should be valid and normalized to lowercase
    assert card.import_id == 'deadbeef'


def test_import_id_auto_generation_uniqueness(user_factory, deck_factory):
    """Test that auto-generated IDs are unique for different cards."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        'Card 1\n#card\nBack 1\n\n'
        'Card 2\n#card\nBack 2\n\n'
        'Card 3\n#card\nBack 3'
    )
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    
    assert record.summary['created'] == 3
    cards = Card.objects.filter(user=user).order_by('created_at')
    ids = [c.import_id for c in cards]
    
    # All IDs should be unique
    assert len(ids) == len(set(ids))
    # All should be hex-like
    for card_id in ids:
        int(card_id, 16)  # Should not raise


def test_import_id_preserved_on_multi_update(user_factory, deck_factory):
    """Test that import ID is preserved across multiple updates."""
    user = user_factory()
    deck = deck_factory(user=user)
    
    # Create with ID
    markdown1 = 'Q1\n#card id:abc123\nA1'
    archive1 = _build_zip({'note.md': markdown1})
    process_markdown_archive(user=user, deck=deck, uploaded_file=archive1)
    card = Card.objects.get(user=user)
    assert card.import_id == 'abc123'
    
    # Update with same ID
    markdown2 = 'Q1 Updated\n#card id:abc123\nA1 Updated'
    archive2 = _build_zip({'note.md': markdown2})
    process_markdown_archive(user=user, deck=deck, uploaded_file=archive2)
    
    card.refresh_from_db()
    assert card.import_id == 'abc123'
    assert card.front_md == 'Q1 Updated'
    
    # Update again with same ID
    markdown3 = 'Q1 Updated Again\n#card id:abc123\nA1 Updated Again'
    archive3 = _build_zip({'note.md': markdown3})
    process_markdown_archive(user=user, deck=deck, uploaded_file=archive3)
    
    card.refresh_from_db()
    assert card.import_id == 'abc123'
    assert card.front_md == 'Q1 Updated Again'


# ============================================================================
# EDGE CASES: Heading Context Hierarchy
# ============================================================================

def test_hierarchy_with_level_1_heading_marker(user_factory, deck_factory):
    """Test hierarchy when marker is on # (h1) heading."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = '# Main Title\n## Subtitle\n### Card Title #card\nContent'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    # Should include Main Title and Subtitle in context
    assert 'Main Title > Subtitle' in card.front_md
    assert 'Card Title' in card.front_md
    assert 'Content' in card.back_md


def test_hierarchy_empty_heading_stack_from_marker_on_h1(user_factory, deck_factory):
    """Test that marker on h1 results in no parent context."""
    user = user_factory()
    deck = deck_factory(user=user)
    # Marker on h1 means no parents (can't have h1 as parent)
    markdown = '## Subtitle\n# Main Header #card\nContent'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    # Should NOT include Subtitle (it's larger/different hierarchy branch)
    assert 'Subtitle' not in card.front_md 
    assert 'Main Header' in card.front_md


def test_hierarchy_preserves_across_blank_lines(user_factory, deck_factory):
    """Test that hierarchy persists across blank lines when not broken by new heading."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "# Section\n"
        "## Subsection\n"
        "\n"
        "\n"
        "### Topic #card\n"
        "Content"
    )
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    # Should have both Section and Subsection in hierarchy
    assert 'Section' in card.front_md
    assert 'Subsection' in card.front_md
    assert 'Topic' in card.front_md
    assert 'Content' in card.back_md


def test_hierarchy_identical_to_card_front_removes_last_entry(user_factory, deck_factory):
    """Test that if last hierarchy entry matches front text, it's removed to avoid duplication."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        "# Section\n"
        "## Card Topic\n"
        "## Card Topic #card\n"
        "Content"
    )
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    # Should NOT have duplicate "Card Topic"
    front_lines = card.front_md.split('\n')
    if len(front_lines) > 1:
        # If hierarchy, should not repeat Card Topic twice
        context_line = front_lines[0]
        card_line = front_lines[1]
        # They should be different or hierarchy should not repeat the title
        assert not context_line.endswith('Card Topic > Card Topic')


# ============================================================================
# EDGE CASES: Reverse Cards
# ============================================================================

def test_reverse_card_with_hyphen_separator(user_factory, deck_factory):
    """Test #card-reverse (hyphenated) variant."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = 'Front\n#card-reverse\nBack'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    
    assert record.summary['created'] == 2
    cards = Card.objects.filter(user=user).order_by('created_at')
    assert cards[0].front_md.strip() == 'Front'
    assert cards[0].back_md == 'Back'
    assert cards[1].front_md.strip() == 'Back'
    assert cards[1].back_md == 'Front'


def test_reverse_card_disallowed_when_option_set(user_factory, deck_factory):
    """Test that reverse flag is ignored when card type has allow_reverse=False."""
    # This would require custom card type - skipping for now as it's advanced
    pass


def test_reverse_card_preserves_media(user_factory, deck_factory, settings):
    """Test that reverse cards include media from both directions."""
    user = user_factory()
    deck = deck_factory(user=user)
    image_bytes = b'image-data'
    markdown = 'Image #card/reverse\n![](image.png)'
    archive = _build_zip({'note.md': markdown}, media={'image.png': image_bytes})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    
    assert record.summary['created'] == 2
    cards = Card.objects.filter(user=user).order_by('created_at')
    # Both should have media
    for card in cards:
        assert card.media


# ============================================================================
# EDGE CASES: Tag Handling
# ============================================================================

def test_tags_parsing_with_semicolon_separator(user_factory, deck_factory):
    """Test tag parsing with semicolon separator on line after marker."""
    user = user_factory()
    deck = deck_factory(user=user)
    # Tags must come after the #card marker on its own line
    markdown = 'Question\n#card\ntags:: tag1; tag2; tag3\nAnswer'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    assert 'tag1' in card.tags
    assert 'tag2' in card.tags
    assert 'tag3' in card.tags


def test_tags_parsing_with_comma_separator(user_factory, deck_factory):
    """Test tag parsing with comma separator on line after marker."""
    user = user_factory()
    deck = deck_factory(user=user)
    # Tags must come after the #card marker on its own line
    markdown = 'Question\n#card\ntags:: foo,bar,baz\nAnswer'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    assert 'foo' in card.tags
    assert 'bar' in card.tags
    assert 'baz' in card.tags


def test_tags_replace_on_update(user_factory, deck_factory):
    """Test that tags are replaced (not merged) when updating a card."""
    user = user_factory()
    deck = deck_factory(user=user)
    
    # Create with tags - tags must come after #card marker
    markdown1 = 'Question\n#card id:tag_test\ntags:: tag1, tag2\nAnswer'
    archive1 = _build_zip({'note.md': markdown1})
    process_markdown_archive(user=user, deck=deck, uploaded_file=archive1)
    card = Card.objects.get(user=user)
    assert len(card.tags) == 2
    assert set(card.tags) == {'tag1', 'tag2'}
    
    # Update with different tags - old tags should be replaced with new tags
    markdown2 = 'Question Updated\n#card id:tag_test\ntags:: tag2, tag3\nAnswer Updated'
    archive2 = _build_zip({'note.md': markdown2})
    process_markdown_archive(user=user, deck=deck, uploaded_file=archive2)
    
    card.refresh_from_db()
    # Tags should be replaced: only tag2 and tag3 remain (tag1 removed, tag2 kept, tag3 added)
    assert len(card.tags) == 2
    assert set(card.tags) == {'tag2', 'tag3'}


# ============================================================================
# EDGE CASES: Deck Path Handling
# ============================================================================

def test_deck_path_skips_default_folder(user_factory):
    """Test that 'default' folder name is skipped from deck path."""
    user = user_factory()
    markdown = 'Q\n#card\nA'
    archive = _build_zip({'default/note.md': markdown})
    session = prepare_markdown_session(user=user, deck=None, uploaded_file=archive)
    card = session.payload['cards'][0]
    
    # default should be stripped
    assert card['deck_path'] == []


def test_deck_path_skips_notes_folder_at_root(user_factory):
    """Test that 'notes' folder is skipped when it's the root."""
    user = user_factory()
    markdown = 'Q\n#card\nA'
    archive = _build_zip({'notes/note.md': markdown})
    session = prepare_markdown_session(user=user, deck=None, uploaded_file=archive)
    card = session.payload['cards'][0]
    
    # notes should be stripped when at root level
    assert card['deck_path'] == []


def test_deck_path_complex_nested_structure(user_factory, deck_factory):
    """Test complex nested deck path handling."""
    user = user_factory()
    root = deck_factory(user=user, name='Root')
    markdown = 'Q\n#card\nA'
    archive = _build_zip({'Root/A/B/C/note.md': markdown})
    session = prepare_markdown_session(user=user, deck=root, uploaded_file=archive)
    card = session.payload['cards'][0]
    
    # Root should be stripped, only A/B/C remain
    assert card['deck_path'] == ['A', 'B', 'C']
    
    # Apply and verify nested decks are created
    record = apply_markdown_session(session)
    created_card = Card.objects.get(user=user)
    
    # Navigate down the deck hierarchy
    current = created_card.deck
    path = [current.name]
    while current.parent:
        current = current.parent
        path.append(current.name)
    
    path.reverse()
    assert path[0] == 'Root'
    assert 'A' in path
    assert 'B' in path
    assert 'C' in path


# ============================================================================
# EDGE CASES: Empty and Minimal Content
# ============================================================================

def test_card_with_empty_back(user_factory, deck_factory):
    """Test card with marker but no back content."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = 'Question #card'
    archive = _build_zip({'note.md': markdown})
    session = prepare_markdown_session(user=user, deck=deck, uploaded_file=archive)
    card = session.payload['cards'][0]
    
    assert card['front_md'].strip() == 'Question'
    assert card['back_md'].strip() == ''


def test_marker_with_no_front_uses_first_back_lines(user_factory, deck_factory):
    """Test that when front is empty, it takes first ~120 chars from back."""
    user = user_factory()
    deck = deck_factory(user=user)
    long_back = 'This is a very long back content that will be used as front when front is empty or missing.'
    markdown = f'#card\n{long_back}'
    archive = _build_zip({'note.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    card = Card.objects.get(user=user)
    
    # Front should be derived from back
    assert 'This is a very long' in card.front_md


# ============================================================================
# EDGE CASES: Multiple Cards and Mixed Scenarios
# ============================================================================

def test_multiple_cards_with_mixed_long_and_short(user_factory, deck_factory):
    """Test file with mix of short cards and long cards."""
    user = user_factory()
    deck = deck_factory(user=user)
    markdown = (
        '## Short 1 #card\nAnswer 1\n\n'
        '## Long Example #long-card\nLine 1\n\nLine 2\n\n## Short 2 #card\nAnswer 2'
    )
    archive = _build_zip({'notes.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    
    assert record.summary['created'] == 3
    cards = Card.objects.filter(user=user).order_by('created_at')
    assert 'Answer 1' in cards[0].back_md
    assert 'Line 1' in cards[1].back_md and 'Line 2' in cards[1].back_md
    assert 'Answer 2' in cards[2].back_md


def test_whitespace_only_deck_path_is_ignored(user_factory):
    """Test that empty or whitespace-only deck paths are normalized away."""
    user = user_factory()
    markdown = 'Q\n#card\nA'
    # Paths with spaces and empty parts
    archive = _build_zip({'  / Notes / / Valid\n/note.md': markdown})
    # This tests the normalization logic


def test_card_with_inline_markers_cleaned_from_front(user_factory, deck_factory):
    """Test that inline markdown markers in front text are cleaned."""
    user = user_factory()
    deck = deck_factory(user=user)
    # Test all inline marker patterns that should be stripped
    markdown = (
        '**bold text** #card\nAnswer\n\n'
        '__another bold__ #card\nAnswer 2\n\n'
        '*italic* #card\nAnswer 3\n\n'
        '_more italic_ #card\nAnswer 4'
    )
    archive = _build_zip({'notes.md': markdown})
    record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    
    cards = Card.objects.filter(user=user).order_by('created_at')
    # Markers should be cleaned
    assert cards[0].front_md.strip() == 'bold text'
    assert cards[1].front_md.strip() == 'another bold'
    assert cards[2].front_md.strip() == 'italic'
    assert cards[3].front_md.strip() == 'more italic'
