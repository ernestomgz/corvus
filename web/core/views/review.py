from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from typing import Any, Dict, List, Optional

from django.contrib.auth.decorators import login_required
from django.db.models.functions import ExtractYear
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from ..models import Card, Deck, Review, SchedulingState, StudySet
from ..scheduling import ensure_state
from ..services.review import (
    StudyScope,
    build_rating_previews,
    get_next_card,
    get_today_summary,
    grade_card_for_user,
)
from ..services.study_sets import fetch_study_sets_with_summaries

REVIEW_HISTORY_SESSION_KEY = 'review_history'
REVIEW_HISTORY_SCOPE_KEY = 'review_history_scope'
REVIEW_HISTORY_LIMIT = 20


def _serialize_state(state: SchedulingState) -> Dict[str, Any]:
    return {
        'queue_status': state.queue_status,
        'interval_days': state.interval_days,
        'ease': state.ease,
        'reps': state.reps,
        'lapses': state.lapses,
        'due_at': state.due_at.isoformat() if state.due_at else None,
        'learning_step_index': state.learning_step_index,
        'last_rating': state.last_rating,
    }


def _restore_state_from_snapshot(state: SchedulingState, snapshot: Dict[str, Any]) -> None:
    state.queue_status = snapshot.get('queue_status', state.queue_status)
    state.interval_days = int(snapshot.get('interval_days', state.interval_days or 0))
    state.ease = float(snapshot.get('ease', state.ease))
    state.reps = int(snapshot.get('reps', state.reps or 0))
    state.lapses = int(snapshot.get('lapses', state.lapses or 0))
    state.learning_step_index = int(snapshot.get('learning_step_index', state.learning_step_index or 0))
    last_rating = snapshot.get('last_rating')
    state.last_rating = int(last_rating) if last_rating is not None else None
    due_at = snapshot.get('due_at')
    if due_at:
        try:
            state.due_at = datetime.fromisoformat(due_at)
        except (TypeError, ValueError):
            state.due_at = state.due_at
    else:
        state.due_at = None
    state.save()


def _get_review_history(request: HttpRequest) -> List[Dict[str, Any]]:
    history = request.session.get(REVIEW_HISTORY_SESSION_KEY, [])
    if not isinstance(history, list):
        history = []
    return history


def _has_history_for_scope(request: HttpRequest, scope: Optional[StudyScope]) -> bool:
    scope_key = scope.history_key() if scope else 'all'
    for entry in _get_review_history(request):
        if entry.get('scope_key', 'all') == scope_key:
            return True
    return False


def _set_review_history(request: HttpRequest, history: List[Dict[str, Any]]) -> None:
    request.session[REVIEW_HISTORY_SESSION_KEY] = history
    request.session.modified = True


def _push_review_history_entry(request: HttpRequest, entry: Dict[str, Any]) -> None:
    history = list(_get_review_history(request))
    history.append(entry)
    if len(history) > REVIEW_HISTORY_LIMIT:
        history = history[-REVIEW_HISTORY_LIMIT:]
    _set_review_history(request, history)


def _pop_review_history_entry(request: HttpRequest, scope: Optional[StudyScope] = None) -> Optional[Dict[str, Any]]:
    history = list(_get_review_history(request))
    scope_key = scope.history_key() if scope else 'all'
    if not history:
        return None
    while history:
        entry = history.pop()
        entry_scope_key = entry.get('scope_key', 'all')
        if entry_scope_key == scope_key:
            _set_review_history(request, history)
            return entry
    _set_review_history(request, history)
    return None


def _ensure_history_scope(request: HttpRequest, scope: Optional[StudyScope]) -> None:
    scope_key = scope.history_key() if scope else 'all'
    last_key = request.session.get(REVIEW_HISTORY_SCOPE_KEY)
    if last_key != scope_key:
        _set_review_history(request, [])
    request.session[REVIEW_HISTORY_SCOPE_KEY] = scope_key
    request.session.modified = True


def _resolve_scope(request: HttpRequest):
    deck_id = request.POST.get('deck_id') or request.GET.get('deck_id')
    study_set_id = request.POST.get('study_set_id') or request.GET.get('study_set_id')
    tag_value = request.POST.get('tag') or request.GET.get('tag')
    pull_ahead = (request.POST.get('pull_ahead') or request.GET.get('pull_ahead')) == '1'
    deck = None
    study_set = None
    if study_set_id:
        try:
            study_set_pk = int(study_set_id)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise Http404('Study set not found') from exc
        try:
            study_set = StudySet.objects.select_related('deck').get(id=study_set_pk, user=request.user)
        except StudySet.DoesNotExist as exc:  # pragma: no cover
            raise Http404('Study set not found') from exc
    if deck_id:
        try:
            deck_pk = int(deck_id)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise Http404('Deck not found') from exc
        try:
            deck = Deck.objects.get(id=deck_pk, user=request.user)
        except Deck.DoesNotExist as exc:  # pragma: no cover
            raise Http404('Deck not found') from exc
    if study_set is not None:
        scope = StudyScope.from_study_set(study_set)
        return scope.deck_target, study_set, scope, pull_ahead
    if deck is not None:
        scope = StudyScope.from_deck(deck)
        return deck, None, scope, pull_ahead
    if tag_value:
        scope = StudyScope(tag=tag_value.strip())
        return None, None, scope, pull_ahead
    return None, None, None, pull_ahead


def _build_dashboard_context(request: HttpRequest, *, scope: Optional[StudyScope], deck, study_set):
    decks = Deck.objects.for_user(request.user)
    study_sets, study_set_summaries = fetch_study_sets_with_summaries(request.user)
    summary = get_today_summary(request.user, scope=scope)
    current_year = timezone.now().year
    selected_year_raw = request.GET.get('year')
    try:
        selected_year = int(selected_year_raw) if selected_year_raw else current_year
    except (TypeError, ValueError):
        selected_year = current_year
    deck_scope_target = scope.deck_target if scope else None
    deck_scope_ids = deck_scope_target.descendant_ids() if deck_scope_target else None
    review_years_qs = Review.objects.filter(user=request.user)
    due_years_qs = SchedulingState.objects.filter(card__user=request.user, due_at__isnull=False)
    if deck_scope_ids:
        review_years_qs = review_years_qs.filter(card__deck_id__in=deck_scope_ids)
        due_years_qs = due_years_qs.filter(card__deck_id__in=deck_scope_ids)
    tag_value = scope.tag_value if scope else None
    if tag_value:
        review_years_qs = review_years_qs.filter(card__tags__contains=[tag_value])
        due_years_qs = due_years_qs.filter(card__tags__contains=[tag_value])
    review_years = set(review_years_qs.annotate(year=ExtractYear('reviewed_at')).values_list('year', flat=True))
    due_years = set(due_years_qs.annotate(year=ExtractYear('due_at')).values_list('year', flat=True))
    available_years = {int(year) for year in review_years.union(due_years) if year is not None}
    available_years.update({current_year, selected_year})
    year_options = sorted(available_years, reverse=True) or [current_year]
    context = {
        'decks': decks,
        'active_deck': deck,
        'active_study_set': study_set,
        'study_sets': study_sets,
        'study_set_summaries': study_set_summaries,
        'summary': summary,
        'heatmap_years': year_options,
        'selected_year': selected_year,
        'user_selected_year': bool(selected_year_raw),
        'active_scope': scope,
    }
    return context


@login_required
def review_today(request: HttpRequest) -> HttpResponse:
    """Backwards compatibility; renders the dashboard view."""
    return review_dashboard(request)


@login_required
def review_dashboard(request: HttpRequest) -> HttpResponse:
    deck, study_set, scope, pull_ahead = _resolve_scope(request)
    _ensure_history_scope(request, scope)
    context = _build_dashboard_context(request, scope=scope, deck=deck, study_set=study_set)
    context['pull_ahead'] = pull_ahead
    return render(request, 'core/review/dashboard.html', context)


@login_required
def review_study(request: HttpRequest) -> HttpResponse:
    deck, study_set, scope, pull_ahead = _resolve_scope(request)
    _ensure_history_scope(request, scope)
    summary = get_today_summary(request.user, scope=scope)
    context = {
        'deck': deck,
        'study_set': study_set,
        'scope': scope,
        'pull_ahead': pull_ahead,
        'summary': summary,
    }
    return render(request, 'core/review/study.html', context)


@login_required
def review_next(request: HttpRequest) -> HttpResponse:
    deck, study_set, scope, pull_ahead = _resolve_scope(request)
    _ensure_history_scope(request, scope)
    card = get_next_card(request.user, deck=deck, scope=scope, allow_ahead=pull_ahead)
    summary = get_today_summary(request.user, deck=deck, scope=scope)
    can_undo = _has_history_for_scope(request, scope)
    if not card:
        return render(
            request,
            'core/review/partials/empty.html',
            {
                'summary': summary,
                'deck': deck,
                'study_set': study_set,
                'scope': scope,
                'pull_ahead': pull_ahead,
                'can_undo': can_undo,
            },
        )
    ensure_state(card)
    now = timezone.now()
    rating_previews = build_rating_previews(card, now=now)
    return render(
        request,
        'core/review/partials/front.html',
        {
            'card': card,
            'deck': deck,
            'study_set': study_set,
            'scope': scope,
            'pull_ahead': pull_ahead,
            'summary': summary,
            'rating_previews': rating_previews,
            'can_undo': can_undo,
        },
    )


@login_required
def review_reveal(request: HttpRequest) -> HttpResponse:
    card_id = request.POST.get('card_id')
    if not card_id:
        raise Http404('card not provided')
    card = get_object_or_404(Card.objects.select_related('deck', 'scheduling_state'), id=card_id, user=request.user)
    deck, study_set, scope, pull_ahead = _resolve_scope(request)
    _ensure_history_scope(request, scope)
    summary = get_today_summary(request.user, deck=deck, scope=scope)
    ensure_state(card)
    now = timezone.now()
    rating_previews = build_rating_previews(card, now=now)
    can_undo = _has_history_for_scope(request, scope)
    return render(
        request,
        'core/review/partials/back.html',
        {
            'card': card,
            'deck': deck,
            'study_set': study_set,
            'scope': scope,
            'pull_ahead': pull_ahead,
            'summary': summary,
            'rating_previews': rating_previews,
            'can_undo': can_undo,
        },
    )


@login_required
def review_grade(request: HttpRequest) -> HttpResponse:
    card_id = request.POST.get('card_id')
    rating = request.POST.get('rating')
    if card_id is None or rating is None:
        raise Http404('Missing card or rating')
    deck, study_set, scope, pull_ahead = _resolve_scope(request)
    _ensure_history_scope(request, scope)
    now = timezone.now()
    try:
        rating_value = int(rating)
    except (TypeError, ValueError):
        raise Http404('Invalid rating')
    card = get_object_or_404(
        Card.objects.select_related('deck', 'scheduling_state'),
        id=card_id,
        user=request.user,
    )
    state_before = ensure_state(card)
    state_snapshot = _serialize_state(state_before)
    tags_before = list(card.tags)
    _, review_record = grade_card_for_user(user=request.user, card_id=card_id, rating=rating_value, now=now)
    scope_key = scope.history_key() if scope else 'all'
    _push_review_history_entry(
        request,
        {
            'card_id': str(card.id),
            'scope_key': scope_key,
            'card_deck_id': card.deck_id,
            'state': state_snapshot,
            'tags': tags_before,
            'review_id': review_record.id,
            'rating': rating_value,
            'study_set_id': study_set.id if study_set else None,
        },
    )
    next_card = get_next_card(request.user, deck=deck, scope=scope, allow_ahead=pull_ahead)
    summary = get_today_summary(request.user, deck=deck, scope=scope)
    can_undo = _has_history_for_scope(request, scope)
    if not next_card:
        return render(
            request,
            'core/review/partials/empty.html',
            {
                'summary': summary,
                'deck': deck,
                'study_set': study_set,
                'scope': scope,
                'pull_ahead': pull_ahead,
                'can_undo': can_undo,
            },
        )
    ensure_state(next_card)
    rating_previews = build_rating_previews(next_card, now=now)
    return render(
        request,
        'core/review/partials/front.html',
        {
            'card': next_card,
            'deck': deck,
            'study_set': study_set,
            'scope': scope,
            'pull_ahead': pull_ahead,
            'summary': summary,
            'rating_previews': rating_previews,
            'can_undo': can_undo,
        },
    )


@login_required
def review_undo(request: HttpRequest) -> HttpResponse:
    deck, study_set, scope, pull_ahead = _resolve_scope(request)
    _ensure_history_scope(request, scope)
    entry = _pop_review_history_entry(request, scope)
    if not entry:
        return review_next(request)
    card = get_object_or_404(
        Card.objects.select_related('deck', 'scheduling_state'),
        id=entry.get('card_id'),
        user=request.user,
    )
    state = ensure_state(card)
    snapshot = entry.get('state', {})
    if isinstance(snapshot, dict):
        _restore_state_from_snapshot(state, snapshot)
    tags_before = entry.get('tags')
    if isinstance(tags_before, list):
        card.tags = list(tags_before)
        card.save(update_fields=['tags'])
    review_id = entry.get('review_id')
    if review_id:
        Review.objects.filter(id=review_id, user=request.user).delete()
    summary = get_today_summary(request.user, deck=deck, scope=scope)
    now = timezone.now()
    rating_previews = build_rating_previews(card, now=now)
    can_undo = _has_history_for_scope(request, scope)
    return render(
        request,
        'core/review/partials/front.html',
        {
            'card': card,
            'deck': deck,
            'study_set': study_set,
            'scope': scope,
            'pull_ahead': pull_ahead,
            'summary': summary,
            'rating_previews': rating_previews,
            'can_undo': can_undo,
        },
    )


@login_required
def review_defer(request: HttpRequest) -> HttpResponse:
    card_id = request.POST.get('card_id')
    days_raw = request.POST.get('days', '')
    try:
        days = int(days_raw)
    except (TypeError, ValueError):
        days = 0
    if card_id is None or days not in {1, 7, 30}:
        raise Http404('Invalid defer request')
    deck, study_set, scope, pull_ahead = _resolve_scope(request)
    _ensure_history_scope(request, scope)
    card = get_object_or_404(Card.objects.select_related('deck', 'scheduling_state'), id=card_id, user=request.user)
    state = ensure_state(card)
    now = timezone.now()
    state.queue_status = 'review'
    state.due_at = now + timedelta(days=days)
    state.learning_step_index = 0
    state.save(update_fields=['queue_status', 'due_at', 'learning_step_index'])
    next_card = get_next_card(request.user, deck=deck, scope=scope, allow_ahead=pull_ahead)
    summary = get_today_summary(request.user, deck=deck, scope=scope)
    can_undo = _has_history_for_scope(request, scope)
    if not next_card:
        return render(
            request,
            'core/review/partials/empty.html',
            {
                'summary': summary,
                'deck': deck,
                'study_set': study_set,
                'scope': scope,
                'pull_ahead': pull_ahead,
                'can_undo': can_undo,
            },
        )
    ensure_state(next_card)
    rating_previews = build_rating_previews(next_card, now=now)
    return render(
        request,
        'core/review/partials/front.html',
        {
            'card': next_card,
            'deck': deck,
            'study_set': study_set,
            'scope': scope,
            'pull_ahead': pull_ahead,
            'summary': summary,
            'rating_previews': rating_previews,
            'can_undo': can_undo,
        },
    )


@login_required
def review_delete(request: HttpRequest) -> HttpResponse:
    card_id = request.POST.get('card_id')
    if card_id is None:
        raise Http404('Missing card')
    deck, study_set, scope, pull_ahead = _resolve_scope(request)
    _ensure_history_scope(request, scope)
    card = get_object_or_404(Card.objects.select_related('deck'), id=card_id, user=request.user)
    card.delete()
    next_card = get_next_card(request.user, deck=deck, scope=scope, allow_ahead=pull_ahead)
    summary = get_today_summary(request.user, deck=deck, scope=scope)
    can_undo = _has_history_for_scope(request, scope)
    if not next_card:
        return render(
            request,
            'core/review/partials/empty.html',
            {
                'summary': summary,
                'deck': deck,
                'study_set': study_set,
                'scope': scope,
                'pull_ahead': pull_ahead,
                'can_undo': can_undo,
            },
        )
    ensure_state(next_card)
    rating_previews = build_rating_previews(next_card, now=timezone.now())
    return render(
        request,
        'core/review/partials/front.html',
        {
            'card': next_card,
            'deck': deck,
            'study_set': study_set,
            'scope': scope,
            'pull_ahead': pull_ahead,
            'summary': summary,
            'rating_previews': rating_previews,
            'can_undo': can_undo,
        },
    )
