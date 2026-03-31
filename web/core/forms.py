from __future__ import annotations

import json
from typing import Iterable

from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory

from .models import Card, Deck, CardType, CardImportFormat, StudySet, UserSettings


def _normalise_tags(raw: Iterable[str]) -> list[str]:
    cleaned = []
    for item in raw:
        trimmed = item.strip()
        if trimmed and trimmed not in cleaned:
            cleaned.append(trimmed)
    return cleaned


class DeckForm(forms.ModelForm):
    class Meta:
        model = Deck
        fields = ['name', 'parent', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full border rounded p-2'}),
            'parent': forms.Select(attrs={'class': 'w-full border rounded p-2'}),
            'description': forms.Textarea(attrs={'class': 'w-full border rounded p-2', 'rows': 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        parent_field = self.fields['parent']
        if user is not None:
            queryset = Deck.objects.for_user(user).order_by('name')
            if self.instance.pk:
                queryset = queryset.exclude(pk=self.instance.pk)
            parent_field.queryset = queryset
        else:
            parent_field.queryset = Deck.objects.none()
        parent_field.empty_label = 'No parent (top level)'

    def save(self, commit: bool = True):
        deck = super().save(commit=False)
        if self.user is not None:
            deck.user = self.user
        if commit:
            deck.save()
        return deck


class StudySetForm(forms.ModelForm):
    class Meta:
        model = StudySet
        fields = ['name', 'kind', 'deck', 'tag', 'decks', 'tags', 'filenames']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full border rounded p-2'}),
            'kind': forms.RadioSelect(attrs={'class': 'flex gap-4 text-sm'}),
            'deck': forms.Select(attrs={'class': 'w-full border rounded p-2'}),
            'tag': forms.TextInput(attrs={'class': 'w-full border rounded p-2', 'placeholder': 'e.g. physics'}),
            'decks': forms.SelectMultiple(attrs={'class': 'w-full border rounded p-2'}),
            'tags': forms.SelectMultiple(attrs={'class': 'w-full border rounded p-2'}),
            'filenames': forms.SelectMultiple(attrs={'class': 'w-full border rounded p-2'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None:
            self.fields['deck'].queryset = Deck.objects.for_user(user).order_by('name')
            self.fields['decks'].queryset = Deck.objects.for_user(user).order_by('name')
            # Populate tags from user's cards
            user_tags = Card.objects.for_user(user).values_list('tags', flat=True)
            all_tags = set()
            for tag_list in user_tags:
                all_tags.update(tag_list)
            self.fields['tags'].choices = [(tag, tag) for tag in sorted(all_tags)]
            # Populate filenames from user's cards
            user_filenames = Card.objects.for_user(user).exclude(source_path__isnull=True).values_list('source_path', flat=True).distinct()
            self.fields['filenames'].choices = [(fn, fn) for fn in sorted(user_filenames)]
        else:
            self.fields['deck'].queryset = Deck.objects.none()
            self.fields['decks'].queryset = Deck.objects.none()
            self.fields['tags'].choices = []
            self.fields['filenames'].choices = []
        self.fields['deck'].required = False
        self.fields['tag'].required = False
        self.fields['decks'].required = False
        self.fields['tags'].required = False
        self.fields['filenames'].required = False

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get('kind')
        deck = cleaned.get('deck')
        tag = (cleaned.get('tag') or '').strip()
        decks = cleaned.get('decks') or []
        tags = cleaned.get('tags') or []
        filenames = cleaned.get('filenames') or []
        name = (cleaned.get('name') or '').strip()
        if kind == StudySet.KIND_DECK:
            if not deck:
                self.add_error('deck', 'Select a deck to study.')
            elif not name:
                cleaned['name'] = deck.full_path()
        elif kind == StudySet.KIND_TAG:
            if not tag:
                self.add_error('tag', 'Enter a tag to study.')
            else:
                cleaned['tag'] = tag
                if not name:
                    cleaned['name'] = f"Tag: {tag}"
        elif kind == StudySet.KIND_CUSTOM:
            if not decks and not tags and not filenames:
                self.add_error(None, 'Select at least one deck, tag, or filename.')
            if not name:
                parts = []
                if decks:
                    parts.append(f"{len(decks)} deck{'s' if len(decks) > 1 else ''}")
                if tags:
                    parts.append(f"{len(tags)} tag{'s' if len(tags) > 1 else ''}")
                if filenames:
                    parts.append(f"{len(filenames)} file{'s' if len(filenames) > 1 else ''}")
                cleaned['name'] = f"Custom: {', '.join(parts)}"
        else:
            self.add_error('kind', 'Choose how you want to study.')
        return cleaned

    def save(self, commit: bool = True):
        study_set = super().save(commit=False)
        if self.user is not None:
            study_set.user = self.user
        if commit:
            study_set.save()
            if study_set.kind == StudySet.KIND_CUSTOM:
                study_set.decks.set(self.cleaned_data['decks'])
        return study_set


class CardForm(forms.ModelForm):
    tags = forms.CharField(
        required=False,
        help_text='Comma-separated tags',
        widget=forms.TextInput(attrs={'class': 'w-full border rounded p-2'}),
    )
    card_type = forms.ModelChoiceField(
        queryset=CardType.objects.none(),
        to_field_name='slug',
        widget=forms.Select(attrs={'class': 'w-full border rounded p-2'}),
        empty_label=None,
    )

    class Meta:
        model = Card
        fields = ['deck', 'card_type', 'front_md', 'back_md', 'tags']
        widgets = {
            'deck': forms.Select(attrs={'class': 'w-full border rounded p-2'}),
            'front_md': forms.Textarea(attrs={'class': 'w-full border rounded p-2 font-mono', 'rows': 5}),
            'back_md': forms.Textarea(attrs={'class': 'w-full border rounded p-2 font-mono', 'rows': 5}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None:
            self.fields['deck'].queryset = Deck.objects.for_user(user).order_by('name')
            type_queryset = CardType.objects.filter(Q(user=user) | Q(user__isnull=True)).order_by('name')
            self.fields['card_type'].queryset = type_queryset
            default_type = type_queryset.filter(slug='basic').first()
            if default_type and 'card_type' not in self.initial:
                self.initial['card_type'] = default_type.slug
        else:
            self.fields['deck'].queryset = Deck.objects.none()
            self.fields['card_type'].queryset = CardType.objects.none()
        if self.instance.pk:
            self.initial['tags'] = ', '.join(self.instance.tags)

    def clean_tags(self):
        raw = self.cleaned_data.get('tags', '')
        if isinstance(raw, list):
            return _normalise_tags(raw)
        parts = raw.split(',') if raw else []
        return _normalise_tags(parts)

    def save(self, commit: bool = True):
        card = super().save(commit=False)
        if self.user is not None:
            card.user = self.user
        card.tags = self.cleaned_data.get('tags', [])
        if commit:
            card.save()
        return card


class CardFilterForm(forms.Form):
    deck = forms.ModelChoiceField(queryset=Deck.objects.none(), required=False)
    tag = forms.CharField(required=False)
    q = forms.CharField(required=False, label='Search')

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields['deck'].queryset = Deck.objects.filter(user=user).order_by('name')
        self.fields['deck'].widget.attrs.update({'class': 'border rounded p-2'})
        self.fields['tag'].widget.attrs.update({'class': 'border rounded p-2', 'placeholder': 'Tag'})
        self.fields['q'].widget.attrs.update({'class': 'border rounded p-2', 'placeholder': 'Search text'})


class CardTypeForm(forms.ModelForm):
    field_schema = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'w-full border rounded p-2 font-mono', 'rows': 4}),
        help_text='JSON list describing the fields for this card type.',
    )

    class Meta:
        model = CardType
        fields = ['name', 'slug', 'description', 'front_template', 'back_template', 'field_schema']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full border rounded p-2'}),
            'slug': forms.TextInput(attrs={'class': 'w-full border rounded p-2'}),
            'description': forms.Textarea(attrs={'class': 'w-full border rounded p-2', 'rows': 3}),
            'front_template': forms.Textarea(attrs={'class': 'w-full border rounded p-2 font-mono', 'rows': 4}),
            'back_template': forms.Textarea(attrs={'class': 'w-full border rounded p-2 font-mono', 'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and 'field_schema' not in self.initial:
            schema = self.instance.field_schema or []
            self.initial['field_schema'] = json.dumps(schema, indent=2)

    def clean_field_schema(self):
        raw = self.cleaned_data.get('field_schema')
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f'Field schema must be valid JSON: {exc}') from exc
        if not isinstance(parsed, list):
            raise forms.ValidationError('Field schema must be a JSON array.')
        return parsed


class CardImportFormatForm(forms.ModelForm):
    options = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'w-full border rounded p-2 font-mono', 'rows': 3}),
        help_text='Optional JSON metadata for this import format.',
    )

    class Meta:
        model = CardImportFormat
        fields = ['name', 'format_kind', 'template', 'options']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full border rounded p-2'}),
            'format_kind': forms.Select(attrs={'class': 'w-full border rounded p-2'}),
            'template': forms.Textarea(attrs={'class': 'w-full border rounded p-2 font-mono', 'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and 'options' not in self.initial:
            self.initial['options'] = json.dumps(self.instance.options or {}, indent=2)

    def clean_options(self):
        raw = self.cleaned_data.get('options')
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f'Options must be valid JSON: {exc}') from exc
        if not isinstance(parsed, dict):
            raise forms.ValidationError('Options must be a JSON object.')
        return parsed


CardImportFormatFormSet = inlineformset_factory(
    CardType,
    CardImportFormat,
    form=CardImportFormatForm,
    extra=1,
    can_delete=True,
)


class KnowledgeMapImportForm(forms.Form):
    json_file = forms.FileField(
        required=False,
        label='JSON file',
        help_text='Upload a JSON file that defines your knowledge map.',
    )
    json_text = forms.CharField(
        required=False,
        label='Or paste JSON',
        widget=forms.Textarea(attrs={'rows': 12}),
        help_text='Paste JSON if you generated the map elsewhere.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['json_file'].widget.attrs.update({'class': 'w-full text-sm'})
        self.fields['json_text'].widget.attrs.update(
            {'class': 'w-full border rounded p-2 font-mono text-xs'}
        )

    def clean(self):
        cleaned = super().clean()
        uploaded = cleaned.get('json_file')
        pasted = (cleaned.get('json_text') or '').strip()
        raw_text = ''
        if uploaded:
            try:
                content = uploaded.read()
            except Exception as exc:  # pragma: no cover - defensive branch
                raise forms.ValidationError(f'Unable to read uploaded file: {exc}') from exc
            try:
                raw_text = content.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise forms.ValidationError('Uploaded file must be UTF-8 encoded JSON.') from exc
        elif pasted:
            raw_text = pasted
        else:
            raise forms.ValidationError('Upload a JSON file or paste JSON into the form.')
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f'Invalid JSON: {exc}') from exc
        cleaned['payload'] = payload
        return cleaned


class UserSettingsForm(forms.ModelForm):
    class Meta:
        model = UserSettings
        fields = [
            'default_deck',
            'default_study_set',
            'new_card_daily_limit',
            'notifications_enabled',
            'theme',
            'plugin_github_enabled',
            'plugin_github_repo',
            'plugin_github_branch',
            'plugin_github_token',
            'plugin_ai_enabled',
            'plugin_ai_provider',
            'plugin_ai_api_key',
            'scheduled_pull_interval',
            'max_delete_threshold',
            'require_recent_pull_before_push',
            'push_preview_required',
            'metadata',
        ]
        widgets = {
            'default_deck': forms.Select(attrs={'class': 'w-full border rounded p-2'}),
            'default_study_set': forms.Select(attrs={'class': 'w-full border rounded p-2'}),
            'new_card_daily_limit': forms.NumberInput(attrs={'class': 'w-full border rounded p-2', 'min': 0}),
            'notifications_enabled': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-indigo-600'}),
            'theme': forms.Select(attrs={'class': 'w-full border rounded p-2'}),
            'plugin_github_enabled': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-indigo-600'}),
            'plugin_github_repo': forms.TextInput(attrs={'class': 'w-full border rounded p-2', 'placeholder': 'owner/repo'}),
            'plugin_github_branch': forms.TextInput(attrs={'class': 'w-full border rounded p-2'}),
            'plugin_github_token': forms.PasswordInput(attrs={'class': 'w-full border rounded p-2', 'placeholder': 'Personal Access Token'}, render_value=False),
            'plugin_ai_enabled': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-indigo-600'}),
            'plugin_ai_provider': forms.TextInput(attrs={'class': 'w-full border rounded p-2', 'placeholder': 'openai / ollama / ...'}),
            'plugin_ai_api_key': forms.PasswordInput(attrs={'class': 'w-full border rounded p-2', 'placeholder': 'API key'}, render_value=False),
            'scheduled_pull_interval': forms.Select(attrs={'class': 'w-full border rounded p-2'}),
            'max_delete_threshold': forms.NumberInput(attrs={'class': 'w-full border rounded p-2', 'min': 0}),
            'require_recent_pull_before_push': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-indigo-600'}),
            'push_preview_required': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-indigo-600'}),
            'metadata': forms.Textarea(attrs={'class': 'w-full border rounded p-2 font-mono text-xs', 'rows': 4}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields['default_deck'].queryset = Deck.objects.for_user(user).order_by('name')
            self.fields['default_study_set'].queryset = StudySet.objects.for_user(user).order_by('name')
        else:
            self.fields['default_deck'].queryset = Deck.objects.none()
            self.fields['default_study_set'].queryset = StudySet.objects.none()
        # Do not expose stored secrets by default.
        self.fields['plugin_github_token'].initial = ''
        self.fields['plugin_ai_api_key'].initial = ''
        self.fields['theme'].widget.choices = [
            ('system', 'System'),
            ('light', 'Light'),
            ('dark', 'Dark'),
        ]
        self.fields['scheduled_pull_interval'].widget.choices = [
            ('off', 'Off'),
            ('hourly', 'Hourly'),
            ('daily', 'Daily'),
        ]

    def clean_metadata(self):
        raw = self.cleaned_data.get('metadata')
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise forms.ValidationError(f'Metadata must be valid JSON: {exc}') from exc
            if not isinstance(parsed, dict):
                raise forms.ValidationError('Metadata must be a JSON object.')
            return parsed
        raise forms.ValidationError('Metadata must be JSON or left empty.')
