from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from collections import defaultdict

from django.db.models import Count

from ..models import Card, Deck


@dataclass
class DeckNode:
    deck: Deck
    total_cards: int
    children: List['DeckNode'] = field(default_factory=list)


def build_deck_tree(user) -> List[DeckNode]:
    decks = list(Deck.objects.for_user(user).select_related('parent').order_by('name'))
    if not decks:
        return []

    card_counts = (
        Card.objects.filter(user=user)
        .values('deck_id')
        .annotate(total=Count('id'))
    )
    count_map = {row['deck_id']: row['total'] for row in card_counts}

    children: dict[int | None, list[Deck]] = defaultdict(list)
    for deck in decks:
        children[deck.parent_id].append(deck)


    def accumulate(deck: Deck) -> DeckNode:
        child_nodes = [accumulate(child) for child in sorted(children.get(deck.id, []), key=lambda d: d.name.lower())]
        total = count_map.get(deck.id, 0) + sum(child.total_cards for child in child_nodes)
        return DeckNode(deck=deck, total_cards=total, children=child_nodes)

    root_decks = sorted(children.get(None, []), key=lambda d: d.name.lower())
    return [accumulate(deck) for deck in root_decks]


def flatten_deck_ids(root: Deck, *, include_self: bool = True) -> list[int]:
    ids = root.descendant_ids(include_self=include_self)
    return ids


def split_deck_path(value: str) -> list[str]:
    return [segment.strip() for segment in value.split('/') if segment and segment.strip()]


def get_deck_by_full_path(user, full_path: str) -> Deck | None:
    parts = split_deck_path(full_path)
    if not parts:
        return None
    current = None
    for part in parts:
        try:
            current = Deck.objects.get(user=user, parent=current, name=part)
        except Deck.DoesNotExist:
            return None
    return current


def plan_deck_path(user, root: Deck | None, path: list[str]) -> list[str]:
    current = root
    planned: list[str] = []
    if current is None and not path:
        return planned
    for name in path:
        try:
            current = Deck.objects.get(user=user, parent=current, name=name)
        except Deck.DoesNotExist:
            base_parts = current.full_path().split('/') if current is not None else []
            planned_parts = [*base_parts, name]
            planned.append('/'.join(part for part in planned_parts if part))
            current = Deck(user=user, parent=current, name=name)
    return planned


def ensure_deck_path(user, root: Deck | None, path: list[str]) -> tuple[Deck, list[Deck]]:
    current = root
    created: list[Deck] = []
    if current is None and not path:
        raise ValueError('Cannot resolve deck path without a root deck or folder hierarchy.')
    for index, name in enumerate(path):
        parent = current
        deck, created_flag = Deck.objects.get_or_create(user=user, parent=parent, name=name)
        if created_flag:
            created.append(deck)
        current = deck
    if current is None:
        raise ValueError('Unable to resolve deck path.')
    return current, created
