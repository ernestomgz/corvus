from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from ..forms import CardFilterForm, CardForm
from ..models import Card
from ..scheduling import ensure_state


@login_required
def card_list(request: HttpRequest) -> HttpResponse:
    form = CardFilterForm(request.GET or None, user=request.user)
    cards = Card.objects.for_user(request.user).select_related('deck', 'scheduling_state')
    if form.is_valid():
        deck = form.cleaned_data.get('deck')
        tag = form.cleaned_data.get('tag')
        query = form.cleaned_data.get('q')
        if deck:
            cards = cards.filter(deck_id__in=deck.descendant_ids())
        if tag:
            cards = cards.filter(tags__contains=[tag.strip()])
        if query:
            cards = cards.filter(Q(front_md__icontains=query) | Q(back_md__icontains=query))
    cards = cards.order_by('-updated_at')
    return render(request, 'core/cards/list.html', {'cards': cards, 'filter_form': form})


@login_required
def card_detail(request: HttpRequest, pk) -> HttpResponse:
    card = get_object_or_404(
        Card.objects.select_related('deck', 'scheduling_state').prefetch_related('external_ids'),
        pk=pk,
        user=request.user,
    )
    ensure_state(card)
    return render(request, 'core/cards/detail.html', {'card': card})


@login_required
def card_create(request: HttpRequest) -> HttpResponse:
    form = CardForm(request.POST or None, user=request.user)
    if request.method == 'POST':
        if form.is_valid():
            card = form.save()
            messages.success(request, 'Card created.')
            return redirect('cards:detail', pk=card.pk)
    return render(request, 'core/cards/form.html', {'form': form, 'card': None})


@login_required
def card_edit(request: HttpRequest, pk) -> HttpResponse:
    card = get_object_or_404(Card, pk=pk, user=request.user)
    form = CardForm(request.POST or None, instance=card, user=request.user)
    if request.method == 'POST':
        if form.is_valid():
            form.save()
            messages.success(request, 'Card updated.')
            return redirect('cards:detail', pk=pk)
    return render(request, 'core/cards/form.html', {'form': form, 'card': card})


@login_required
def card_delete(request: HttpRequest, pk) -> HttpResponse:
    card = get_object_or_404(Card, pk=pk, user=request.user)
    if request.method == 'POST':
        card.delete()
        messages.success(request, 'Card deleted.')
        return redirect('cards:list')
    return render(request, 'core/cards/confirm_delete.html', {'card': card})
