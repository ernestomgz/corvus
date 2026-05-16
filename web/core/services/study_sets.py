from __future__ import annotations

from typing import Iterable, Tuple

from ..models import StudySet
from .review import StudyScope, TodaySummary, get_today_summary


def summarize_study_sets(user, study_sets: Iterable[StudySet]) -> dict[int, TodaySummary]:
    summaries: dict[int, TodaySummary] = {}
    for study_set in study_sets:
        scope = StudyScope.from_study_set(study_set)
        summary = get_today_summary(user, scope=scope)
        summaries[study_set.id] = summary
    return summaries


def fetch_study_sets_with_summaries(user) -> Tuple[list[StudySet], dict[int, TodaySummary]]:
    study_sets = list(
        StudySet.objects.for_user(user).select_related('deck', 'source_root_deck').order_by('-is_favorite', 'name')
    )
    summaries = summarize_study_sets(user, study_sets)
    return study_sets, summaries
