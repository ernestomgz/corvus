import pytest
from datetime import timedelta
from django.utils import timezone

from core.scheduling import ensure_state, grade_card

pytestmark = pytest.mark.django_db


def test_new_card_learning_steps(card_factory):
    card = card_factory()
    state = ensure_state(card)
    now = timezone.now()

    grade_card(card, 2, now=now)
    state.refresh_from_db()
    assert state.queue_status == 'learn'
    assert state.learning_step_index == 1
    assert abs((state.due_at - now).total_seconds() - 600) < 5

    grade_card(card, 2, now=now + timedelta(minutes=10))
    state.refresh_from_db()
    assert state.queue_status == 'review'
    assert state.learning_step_index == 0
    assert state.interval_days == 1
    assert abs((state.due_at - (now + timedelta(minutes=10))).total_seconds() - 24 * 3600) < 60

    other = card_factory()
    other_state = ensure_state(other)
    grade_card(other, 3, now=now)
    other_state.refresh_from_db()
    assert other_state.queue_status == 'review'
    assert other_state.interval_days == 4


def test_new_card_again_uses_first_learning_step(card_factory):
    card = card_factory()
    state = ensure_state(card)
    now = timezone.now()

    grade_card(card, 0, now=now)
    state.refresh_from_db()
    assert state.queue_status == 'learn'
    assert state.learning_step_index == 0
    assert abs((state.due_at - now).total_seconds() - 60) < 5

    grade_card(card, 2, now=now + timedelta(minutes=1))
    state.refresh_from_db()
    assert state.queue_status == 'learn'
    assert state.learning_step_index == 1
    assert abs((state.due_at - (now + timedelta(minutes=1))).total_seconds() - 600) < 5


def test_review_grade_transitions(card_factory):
    now = timezone.now()

    card = card_factory()
    state = ensure_state(card)
    state.queue_status = 'review'
    state.interval_days = 5
    state.ease = 2.5
    state.reps = 3
    state.lapses = 0
    state.due_at = now
    state.save()

    grade_card(card, 0, now=now)
    state.refresh_from_db()
    assert state.queue_status == 'relearn'
    assert abs((state.due_at - now).total_seconds() - 600) < 5
    assert state.ease == pytest.approx(2.3)
    assert state.lapses == 1

    state.queue_status = 'review'
    state.interval_days = 5
    state.ease = 2.5
    state.lapses = 0
    state.due_at = now
    state.save()
    grade_card(card, 1, now=now)
    state.refresh_from_db()
    assert state.interval_days == 6
    assert state.ease == pytest.approx(2.35)

    state.queue_status = 'review'
    state.interval_days = 5
    state.ease = 2.5
    state.lapses = 0
    state.due_at = now
    state.save()
    grade_card(card, 2, now=now)
    state.refresh_from_db()
    assert state.interval_days == 12
    assert state.ease == pytest.approx(2.5)

    state.queue_status = 'review'
    state.interval_days = 5
    state.ease = 2.5
    state.lapses = 0
    state.due_at = now
    state.save()
    grade_card(card, 3, now=now)
    state.refresh_from_db()
    assert state.interval_days == 17
    assert state.ease == pytest.approx(2.65)


def test_lapse_relearn_and_leech(card_factory):
    card = card_factory(tags=[])
    state = ensure_state(card)
    state.queue_status = 'review'
    state.interval_days = 5
    state.ease = 2.5
    state.lapses = 7
    state.due_at = timezone.now()
    state.save()

    grade_card(card, 0, now=timezone.now())
    state.refresh_from_db()
    card.refresh_from_db()

    assert state.queue_status == 'relearn'
    assert state.lapses == 8
    assert state.ease == pytest.approx(2.3)
    assert 'leech' in card.tags
