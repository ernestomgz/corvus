from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone

from .models import Card, SchedulingState


@dataclass(frozen=True)
class SchedulerConfig:
    learning_steps_minutes: list[int]
    graduating_interval_days: int
    easy_bonus_days: int
    hard_min_days: int
    hard_interval_factor: float
    easy_interval_factor: float
    hard_graduating_interval_factor: float
    initial_ease: float
    lapse_step_minutes: int
    leech_threshold: int
    bury_siblings: bool
    day_cutoff_hour: int
    new_limit: int
    review_limit: int


@dataclass
class GradeResult:
    state: SchedulingState
    interval_before: int
    interval_after: int
    ease_before: float
    ease_after: float
    elapsed_days: int
    rating: int
    became_leech: bool
    was_lapse: bool


def get_scheduler_config() -> SchedulerConfig:
    cfg = settings.SCHEDULER_DEFAULTS
    return SchedulerConfig(
        learning_steps_minutes=list(cfg['learning_steps_minutes']),
        graduating_interval_days=cfg['graduating_interval_days'],
        easy_bonus_days=cfg['easy_bonus_days'],
        hard_min_days=cfg.get('hard_min_days', 1),
        hard_interval_factor=cfg.get('hard_interval_factor', 1.2),
        easy_interval_factor=cfg.get('easy_interval_factor', 1.3),
        hard_graduating_interval_factor=cfg.get('hard_graduating_interval_factor', 1.2),
        initial_ease=cfg['initial_ease'],
        lapse_step_minutes=cfg['lapse_step_minutes'],
        leech_threshold=cfg['leech_threshold'],
        bury_siblings=cfg['bury_siblings'],
        day_cutoff_hour=cfg['day_cutoff_hour'],
        new_limit=cfg['new_limit'],
        review_limit=cfg['review_limit'],
    )


def _minutes_delta(minutes: int) -> timedelta:
    return timedelta(minutes=minutes)


def _elapsed_days(state: SchedulingState, now: datetime) -> int:
    if state.due_at is None:
        return 0
    delta = timezone.localtime(now) - timezone.localtime(state.due_at)
    return max(0, int(delta.total_seconds() // 86400))


def ensure_state(card: Card, config: Optional[SchedulerConfig] = None) -> SchedulingState:
    config = config or get_scheduler_config()
    state, _ = SchedulingState.objects.get_or_create(
        card=card,
        defaults={
            'ease': config.initial_ease,
            'queue_status': 'new',
        },
    )
    return state


def grade_card(card: Card, rating: int, *, now: Optional[datetime] = None, config: Optional[SchedulerConfig] = None) -> GradeResult:
    if rating not in (0, 1, 2, 3):
        raise ValueError('rating must be 0..3')
    config = config or get_scheduler_config()
    now = now or timezone.now()
    state = ensure_state(card, config)

    interval_before = state.interval_days
    ease_before = state.ease
    elapsed_days = _elapsed_days(state, now)
    became_leech, was_lapse = _apply_rating(card, state, rating, now, config)

    state.last_rating = rating
    state.save()

    return GradeResult(
        state=state,
        interval_before=interval_before,
        interval_after=state.interval_days,
        ease_before=ease_before,
        ease_after=state.ease,
        elapsed_days=elapsed_days,
        rating=rating,
        became_leech=became_leech,
        was_lapse=was_lapse,
    )


def simulate_rating(state: SchedulingState, rating: int, *, now: Optional[datetime] = None, config: Optional[SchedulerConfig] = None) -> SchedulingState:
    """
    Return an in-memory clone of ``state`` representing the outcome of ``rating`` without persisting it.
    """
    from copy import deepcopy

    config = config or get_scheduler_config()
    now = now or timezone.now()
    clone = deepcopy(state)
    clone.id = None
    clone.card = state.card
    temp_card = deepcopy(state.card)
    _apply_rating(temp_card, clone, rating, now, config, persist=False)
    return clone


def _apply_rating(card: Card, state: SchedulingState, rating: int, now: datetime, config: SchedulerConfig, *, persist: bool = True) -> tuple[bool, bool]:
    became_leech = False
    was_lapse = False

    if state.queue_status in ('new', 'learn'):
        _handle_learning(card, state, rating, now, config)
    elif state.queue_status == 'review':
        was_lapse = _handle_review(card, state, rating, now, config)
    elif state.queue_status == 'relearn':
        _handle_relearn(card, state, rating, now, config)
    else:
        _handle_learning(card, state, rating, now, config)

    if state.lapses >= config.leech_threshold and 'leech' not in card.tags:
        if persist:
            card.tags.append('leech')
            card.save(update_fields=['tags'])
        became_leech = True

    return became_leech, was_lapse

def _handle_learning(card: Card, state: SchedulingState, rating: int, now: datetime, config: SchedulerConfig) -> None:
    steps = config.learning_steps_minutes or [1]
    state.queue_status = 'learn'

    # Keep the raw index so we can detect "past last step" (used to graduate).
    raw_index = max(state.learning_step_index, 0)
    max_index = max(len(steps) - 1, 0)

    # If we are beyond the last step, we should graduate on any successful response.
    # (Again should still reset learning.)
    if raw_index > max_index:
        if rating == 0:  # Again
            state.learning_step_index = 0
            state.due_at = now + _minutes_delta(steps[0])
            return
        # Successful response after completing learning steps -> graduate.
        _graduate_to_review(state, now, config, config.graduating_interval_days)
        return

    # Normal case: we're within steps.
    index = raw_index

    if rating == 0:  # Again
        state.learning_step_index = 0
        state.due_at = now + _minutes_delta(steps[0])
        return

    if rating == 1:  # Hard
        state.learning_step_index = index
        state.due_at = now + _minutes_delta(steps[index])
        return

    if rating == 2:  # Good
        if index < max_index:
            # Advance to the next step and schedule the next step's delay.
            state.learning_step_index = index + 1
            state.due_at = now + _minutes_delta(steps[index + 1])
            return

        _graduate_to_review(state, now, config, config.graduating_interval_days)
        return

    if rating == 3:  # Easy
        _graduate_to_review(state, now, config, config.easy_bonus_days)


def _graduate_to_review(state: SchedulingState, now: datetime, config: SchedulerConfig, interval_days: int) -> None:
    state.queue_status = 'review'
    state.learning_step_index = 0
    state.reps += 1
    state.interval_days = max(1, int(round(interval_days)))
    state.due_at = now + timedelta(days=state.interval_days)


def _handle_review(card: Card, state: SchedulingState, rating: int, now: datetime, config: SchedulerConfig) -> bool:
    if rating == 0:  # Again
        state.reps += 1
        state.lapses += 1
        state.queue_status = 'relearn'
        state.learning_step_index = 0
        state.due_at = now + _minutes_delta(config.lapse_step_minutes)
        state.ease = max(1.3, state.ease - 0.2)
        return True

    state.reps += 1
    if rating == 1:  # Hard
        state.ease = max(1.3, state.ease - 0.15)
        base = max(state.interval_days, 1)
        hard_days = max(config.hard_min_days, int(round(base * config.hard_interval_factor)))
        new_interval = hard_days
    elif rating == 2:  # Good
        base = max(state.interval_days, config.graduating_interval_days)
        new_interval = max(1, int(round(base * state.ease)))
    else:  # Easy
        state.ease = max(1.3, state.ease + 0.15)
        base = max(state.interval_days, config.graduating_interval_days)
        base_interval = max(1, int(round(base * state.ease)))
        bonus_interval = max(1, int(round(base * state.ease * config.easy_interval_factor)))
        new_interval = max(base_interval + config.easy_bonus_days, bonus_interval)

    state.interval_days = new_interval
    state.queue_status = 'review'
    state.learning_step_index = 0
    state.due_at = now + timedelta(days=state.interval_days)
    return False


def _handle_relearn(card: Card, state: SchedulingState, rating: int, now: datetime, config: SchedulerConfig) -> None:
    if rating in (0, 1):
        state.due_at = now + _minutes_delta(config.lapse_step_minutes)
        state.learning_step_index = 0
        return

    previous_interval = max(state.interval_days, 1)
    if rating == 2:
        new_interval = max(1, int(round(previous_interval * 0.7)))
    else:
        state.ease = max(1.3, state.ease + 0.15)
        new_interval = max(1, int(round(previous_interval * 0.7))) + config.easy_bonus_days

    state.interval_days = new_interval
    state.queue_status = 'review'
    state.learning_step_index = 0
    state.due_at = now + timedelta(days=state.interval_days)
    state.reps += 1
