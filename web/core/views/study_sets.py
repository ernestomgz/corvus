from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from ..forms import DeckForm, StudySetForm
from ..models import Deck, StudySet
from ..services.decks import build_deck_tree
from ..services.study_sets import fetch_study_sets_with_summaries


def _study_set_context(request: HttpRequest, *, include_decks: bool = False, study_set_form=None) -> dict[str, object]:
    study_sets, study_set_summaries = fetch_study_sets_with_summaries(request.user)
    context: dict[str, object] = {
        'study_sets': study_sets,
        'study_set_summaries': study_set_summaries,
        'study_set_form': study_set_form or StudySetForm(user=request.user),
    }
    if include_decks:
        context['deck_tree'] = build_deck_tree(request.user)
        context['form'] = DeckForm(user=request.user)
        context['study_set_form'] = study_set_form or StudySetForm(user=request.user)
    return context


@login_required
def study_set_create(request: HttpRequest) -> HttpResponse:
    if request.method != 'POST':
        return redirect('decks:list')
    form = StudySetForm(request.POST, user=request.user)
    if form.is_valid():
        study_set = form.save()
        if study_set.kind == StudySet.KIND_DECK:
            study_set.is_favorite = True
            study_set.save(update_fields=['is_favorite'])
        messages.success(request, 'Study set created.')
        context = _study_set_context(request)
        if getattr(request, 'htmx', False):
            return render(request, 'core/decks/partials/study_sets_panel.html', context)
        return redirect('decks:list')
    if getattr(request, 'htmx', False):
        context = _study_set_context(request, study_set_form=form)
        return render(request, 'core/decks/partials/study_sets_panel.html', context)
    deck_tree_context = _study_set_context(request, include_decks=True, study_set_form=form)
    return render(request, 'core/decks/list.html', deck_tree_context)


@login_required
def study_set_edit(request: HttpRequest, pk: int) -> HttpResponse:
    study_set = get_object_or_404(StudySet, pk=pk, user=request.user)
    if request.method == 'POST':
        form = StudySetForm(request.POST, instance=study_set, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Study set updated.')
            context = _study_set_context(request)
            if getattr(request, 'htmx', False):
                return render(request, 'core/decks/partials/study_sets_panel.html', context)
            return redirect('decks:list')
        if getattr(request, 'htmx', False):
            context = _study_set_context(request, study_set_form=form)
            context['editing_study_set'] = study_set
            return render(request, 'core/decks/partials/study_sets_panel.html', context)
        deck_tree_context = _study_set_context(request, include_decks=True, study_set_form=form)
        deck_tree_context['editing_study_set'] = study_set
        return render(request, 'core/decks/list.html', deck_tree_context)
    else:
        form = StudySetForm(instance=study_set, user=request.user)
        context = _study_set_context(request, study_set_form=form)
        context['editing_study_set'] = study_set
        if getattr(request, 'htmx', False):
            return render(request, 'core/decks/partials/study_sets_panel.html', context)
        deck_tree_context = _study_set_context(request, include_decks=True, study_set_form=form)
        deck_tree_context['editing_study_set'] = study_set
        return render(request, 'core/decks/list.html', deck_tree_context)


@login_required
def study_set_delete(request: HttpRequest, pk: int) -> HttpResponse:
    study_set = get_object_or_404(StudySet, pk=pk, user=request.user)
    if request.method == 'POST':
        study_set.delete()
        messages.success(request, 'Study set removed.')
        context = _study_set_context(request)
        if getattr(request, 'htmx', False):
            return render(request, 'core/decks/partials/study_sets_panel.html', context)
        return redirect('decks:list')
    return redirect('decks:list')


@login_required
def study_set_toggle_pin(request: HttpRequest, pk: int) -> HttpResponse:
    study_set = get_object_or_404(StudySet, pk=pk, user=request.user)
    if request.method != 'POST':
        return redirect('decks:list')
    study_set.is_favorite = not study_set.is_favorite
    study_set.save(update_fields=['is_favorite'])
    status = 'pinned' if study_set.is_favorite else 'unpinned'
    messages.success(request, f'Study set {status}.')
    context = _study_set_context(request)
    if getattr(request, 'htmx', False):
        return render(request, 'core/decks/partials/study_sets_panel.html', context)
    return redirect('decks:list')


@login_required
def study_set_toggle_deck(request: HttpRequest, deck_id: int) -> HttpResponse:
    deck = get_object_or_404(Deck, pk=deck_id, user=request.user)
    if request.method != 'POST':
        return redirect('decks:list')
    study_set, created = StudySet.objects.get_or_create(
        user=request.user,
        kind=StudySet.KIND_DECK,
        deck=deck,
        defaults={'name': deck.full_path(), 'is_favorite': True},
    )
    if created:
        message = f"Deck '{deck.full_path()}' added to study sets."
    else:
        study_set.is_favorite = not study_set.is_favorite
        study_set.save(update_fields=['is_favorite'])
        if study_set.is_favorite:
            message = f"Deck '{deck.full_path()}' pinned to study sets."
        else:
            message = f"Deck '{deck.full_path()}' unpinned. It remains available under Custom Study."
    messages.success(request, message)
    context = _study_set_context(request)
    if getattr(request, 'htmx', False):
        return render(request, 'core/decks/partials/study_sets_panel.html', context)
    return redirect('decks:list')
