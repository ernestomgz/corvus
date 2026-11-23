import pytest
from django.urls import reverse

from core.models import NoteSource, StudySet
from tests.factories import CardFactory, DeckFactory, StudySetFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_study_scope_filters_by_tag(client, user_factory, deck_factory, card_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    tag_value = 'km:english:a1.basic-verbs'
    matching = card_factory(user=user, deck=deck, front_md='Verb card', tags=[tag_value])
    card_factory(user=user, deck=deck, front_md='Other card', tags=['km:math:calc'])
    assert client.login(email=user.email, password='password123')

    url = reverse('review:next') + f'?tag={tag_value}'
    response = client.get(url)
    assert response.status_code == 200
    assert 'Verb card' in response.content.decode('utf-8')
    assert 'Other card' not in response.content.decode('utf-8')


def test_study_page_renders_with_scope(client, user_factory, deck_factory):
    user = user_factory()
    deck = deck_factory(user=user)
    assert client.login(email=user.email, password='password123')
    url = reverse('review:study') + f'?deck_id={deck.id}'
    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode('utf-8')
    assert 'Focused review' in body
    assert str(deck.full_path()) in body


def test_custom_study_set_or_logic():
    user = UserFactory()
    deck1 = DeckFactory(user=user, name='Deck1')
    deck2 = DeckFactory(user=user, name='Deck2')
    card1 = CardFactory(user=user, deck=deck1, tags=['tag1'], source_path='file1.md')
    card2 = CardFactory(user=user, deck=deck2, tags=['tag2'], source_path='file2.md')
    card3 = CardFactory(user=user, deck=deck1, tags=['tag3'], source_path='file3.md')

    study_set = StudySetFactory(user=user, kind=StudySet.KIND_CUSTOM)
    study_set.decks.set([deck1])
    study_set.tags = ['tag2']
    study_set.source_paths = ['file3.md']
    study_set.save()

    from core.services.review import StudyScope, _scoped_states
    scope = StudyScope.from_study_set(study_set)
    states = _scoped_states(user, scope=scope)
    card_ids = set(states.values_list('card_id', flat=True))

    # Should include card1 (deck1), card2 (tag2), card3 (file3.md)
    assert card1.id in card_ids
    assert card2.id in card_ids
    assert card3.id in card_ids


def test_custom_study_set_empty():
    user = UserFactory()
    study_set = StudySetFactory(user=user, kind=StudySet.KIND_CUSTOM)
    # No decks, tags, or source paths

    from core.services.review import StudyScope, _scoped_states
    scope = StudyScope.from_study_set(study_set)
    states = _scoped_states(user, scope=scope)
    assert states.count() == 0


def test_custom_study_set_source_paths_can_be_limited_to_root_deck():
    user = UserFactory()
    root = DeckFactory(user=user, name='STEM')
    root_child = DeckFactory(user=user, name='Science', parent=root)
    other_root = DeckFactory(user=user, name='Other')
    matching = CardFactory(user=user, deck=root_child, source_path='Operations.md')
    outside_root = CardFactory(user=user, deck=other_root, source_path='Operations.md')

    study_set = StudySetFactory(user=user, kind=StudySet.KIND_CUSTOM)
    study_set.source_paths = ['Operations.md']
    study_set.source_root_deck = root
    study_set.save()

    from core.services.review import StudyScope, _scoped_states
    scope = StudyScope.from_study_set(study_set)
    card_ids = set(_scoped_states(user, scope=scope).values_list('card_id', flat=True))

    assert matching.id in card_ids
    assert outside_root.id not in card_ids


def test_preset_note_type_keeps_review_actions_in_the_selected_source_scope(client, deck_factory, card_factory):
    root = deck_factory(name='STEM')
    user = root.user
    questions = card_factory(user=user, deck=root, source_path='BJT.md', front_md='Question front')
    exercise = card_factory(user=user, deck=root, source_path='Exercises.md', front_md='Exercise front')
    exam = card_factory(user=user, deck=root, source_path='Exam.md', front_md='Exam front')
    outside = deck_factory(user=user, name='Outside')
    card_factory(user=user, deck=outside, source_path='Exercises.md', front_md='Outside root')
    for path, kind in [('Exercises.md', 'exercises'), ('Exam.md', 'exam-practice')]:
        NoteSource.objects.create(user=user, source_root_deck=root, source_path=path, note_type=kind)
    preset = StudySetFactory(user=user, kind=StudySet.KIND_CUSTOM, source_root_deck=root, source_paths=[
        questions.source_path, exercise.source_path, exam.source_path,
    ])
    client.force_login(user)
    deck_page = client.get(reverse('decks:list')).content.decode()
    assert f'?study_set_id={preset.id}"' in deck_page
    assert 'Questions only' in deck_page
    assert 'Exercises only' in deck_page
    assert 'Exam practice only' in deck_page

    scope = {'study_set_id': preset.id, 'note_type': 'exercises'}
    page = client.get(reverse('review:study'), scope).content.decode()
    assert 'Exercises only' in page and 'note_type=exercises' in page
    front = client.get(reverse('review:next'), scope).content.decode()
    assert str(exercise.id) in front and str(questions.id) not in front
    assert 'name="note_type" value="exercises"' in front

    wrong = client.post(reverse('review:grade'), {**scope, 'card_id': questions.id, 'rating': 2})
    assert wrong.status_code == 404
    reveal = client.post(reverse('review:reveal'), {**scope, 'card_id': exercise.id})
    assert b'name="note_type" value="exercises"' in reveal.content
    graded = client.post(reverse('review:grade'), {**scope, 'card_id': exercise.id, 'rating': 2})
    assert graded.status_code == 200
    assert b'note_type=exercises' in graded.content
    undone = client.post(reverse('review:undo'), scope)
    assert str(exercise.id).encode() in undone.content
