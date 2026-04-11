import pytest

from core.services.decks import plan_deck_path

pytestmark = pytest.mark.django_db


def test_plan_deck_path_handles_missing_nested_segments(deck_factory, user_factory):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)

    planned = plan_deck_path(user, root, ['Math', 'Functions'])

    assert planned == ['STEM/Math', 'STEM/Math/Functions']


def test_plan_deck_path_uses_existing_prefix_before_planning_tail(deck_factory, user_factory):
    user = user_factory()
    root = deck_factory(user=user, name='STEM', parent=None)
    math = deck_factory(user=user, name='Math', parent=root)

    planned = plan_deck_path(user, root, ['Math', 'Functions', 'Limits'])

    assert planned == ['STEM/Math/Functions', 'STEM/Math/Functions/Limits']
