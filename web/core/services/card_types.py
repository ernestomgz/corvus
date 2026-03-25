from __future__ import annotations

from typing import Any

import re

from django.db import models
from django.db.models import Q

from core.models import CardImportFormat, CardType

_PLACEHOLDER = re.compile(r'{{\s*([a-zA-Z0-9_.-]+)\s*}}')


def accessible_types(user) -> "models.QuerySet[CardType]":
    return CardType.objects.filter(Q(user=user) | Q(user__isnull=True))


def resolve_card_type(user, token: Any | None) -> CardType:
    queryset = accessible_types(user)
    if token is None:
        try:
            return queryset.get(slug='basic')
        except CardType.DoesNotExist:
            ensure_builtin_card_types()
            refreshed = accessible_types(user)
            candidate = refreshed.filter(slug='basic').first() or refreshed.order_by('name').first()
            if not candidate:
                raise ValueError('No card types available')
            return candidate
    if isinstance(token, int):
        try:
            return queryset.get(id=token)
        except CardType.DoesNotExist as exc:
            raise ValueError('Card type not found') from exc
    token_str = str(token).strip()
    try:
        card_type_id = int(token_str)
    except (TypeError, ValueError):
        card_type_id = None
    if card_type_id is not None:
        try:
            return queryset.get(id=card_type_id)
        except CardType.DoesNotExist as exc:
            raise ValueError('Card type not found') from exc
    try:
        return queryset.get(slug=token_str)
    except CardType.DoesNotExist as exc:
        ensure_builtin_card_types()
        refreshed = accessible_types(user)
        try:
            return refreshed.get(slug=token_str)
        except CardType.DoesNotExist as exc2:
            raise ValueError('Card type not found') from exc2


def normalize_field_values(card_type: CardType, values: dict[str, Any]) -> dict[str, str]:
    incoming = {str(key).lower(): value for key, value in (values or {}).items()}
    normalized: dict[str, str] = {}
    for field in card_type.field_schema:
        key = field.get('key')
        if not key:
            continue
        aliases = {key.lower()}
        label = field.get('label')
        if isinstance(label, str):
            aliases.add(label.lower())
        for alias in aliases:
            if alias in incoming:
                value = incoming[alias]
                normalized[key] = _stringify(value)
                break
        else:
            normalized[key] = ''
    for original_key, original_value in (values or {}).items():
        key_str = str(original_key)
        if key_str not in normalized:
            normalized[key_str] = _stringify(original_value)
    return normalized


def render_card_faces(card_type: CardType, field_values: dict[str, Any], *, context: dict | None = None) -> tuple[str, str]:
    data = field_values or {}
    ctx = context or {}
    note_context = ctx.get('note') or {}
    ctx.setdefault('note', note_context)
    note_context.setdefault('path', '')
    note_context.setdefault('anchor', '')
    front = _render_template(card_type.front_template, data, ctx)
    back = _render_template(card_type.back_template, data, ctx)
    return front.strip(), back.strip()


def _render_template(template: str, field_values: dict[str, Any], context: dict[str, Any]) -> str:
    def replace(match: re.Match) -> str:
        token = (match.group(1) or '').strip()
        if not token:
            return ''
        if token.startswith('note.'):
            _, _, attr = token.partition('.')
            return _stringify(context.get('note', {}).get(attr, ''))
        direct = _lookup_value(field_values, token)
        if direct is not None:
            return _stringify(direct)
        return ''

    return _PLACEHOLDER.sub(replace, template or '')


def _lookup_value(data: dict[str, Any], key: str) -> Any | None:
    lower = key.lower()
    for candidate_key, candidate_value in data.items():
        if candidate_key == key or str(candidate_key).lower() == lower:
            return candidate_value
    if lower == 'title':
        for candidate_key, candidate_value in data.items():
            if str(candidate_key).lower() == 'front':
                return candidate_value
    return None


def _stringify(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


_DEFAULT_FRONT_BACK_SCHEMA = [
    {'key': 'front', 'label': 'Front'},
    {'key': 'back', 'label': 'Back'},
    {'key': 'hierarchy', 'label': 'Hierarchy'},
    {'key': 'title', 'label': 'Title'},
    {'key': 'context', 'label': 'Context'},
]


BUILTIN_CARD_TYPES = [
    {
        'slug': 'basic',
        'name': 'Basic',
        'description': 'Single front/back Markdown note',
        'field_schema': list(_DEFAULT_FRONT_BACK_SCHEMA),
        'front_template': '{{hierarchy}}\n{{title}}',
        'back_template': '{{back}}',
        'formats': [
            {
                'name': 'Heading marker',
                'template': '{{hierarchy}}\n## {{title}} #card\n{{back}}',
                'options': {'marker': '#card'},
            },
            {
                'name': 'Inline marker',
                'template': '{{hierarchy}}\n{{title}} #card\n{{back}}',
                'options': {'marker': '#card'},
            },
            {
                'name': 'Line marker',
                'template': '{{hierarchy}}\n{{title}}\n#card\n{{back}}',
                'options': {'marker': '#card'},
            },
            {
                'name': 'Marker with metadata',
                'template': '{{hierarchy}}\n## {{title}} #card\nid:: {{external}}\ntags:: {{tags}}\n\n{{back}}',
                'options': {
                    'marker': '#card',
                    'external_id_field': 'external',
                    'tags_field': 'tags',
                    'anchor_field': 'external',
                },
            },
        ],
    },
    {
        'slug': 'long-card',
        'name': 'Long Card',
        'description': 'Front/back Markdown note with multi-paragraph long back content',
        'field_schema': list(_DEFAULT_FRONT_BACK_SCHEMA),
        'front_template': '{{hierarchy}}\n{{title}}',
        'back_template': '{{back}}',
        'formats': [
            {
                'name': 'Long card marker',
                'template': '{{hierarchy}}\n{{title}} #long-card\n{{back}}',
                'options': {'marker': '#long-card'},
            },
            {
                'name': 'Long card line marker',
                'template': '{{hierarchy}}\n{{title}}\n#long-card\n{{back}}',
                'options': {'marker': '#long-card'},
            },
        ],
    },
    {
        'slug': 'basic_image_front',
        'name': 'Basic (Image on Front)',
        'description': 'Front/back card optimized for media on front',
        'field_schema': list(_DEFAULT_FRONT_BACK_SCHEMA),
        'front_template': '{{front}}',
        'back_template': '{{back}}',
        'formats': [],
    },
    {
        'slug': 'basic_image_back',
        'name': 'Basic (Image on Back)',
        'description': 'Front/back card optimized for media on back',
        'field_schema': list(_DEFAULT_FRONT_BACK_SCHEMA),
        'front_template': '{{front}}',
        'back_template': '{{back}}',
        'formats': [],
    },
    {
        'slug': 'cloze',
        'name': 'Cloze',
        'description': 'Cloze-style deletions',
        'field_schema': list(_DEFAULT_FRONT_BACK_SCHEMA),
        'front_template': '{{front}}',
        'back_template': '{{back}}',
        'formats': [],
    },
    {
        'slug': 'problem',
        'name': 'Problem',
        'description': 'Problem/solution layout',
        'field_schema': list(_DEFAULT_FRONT_BACK_SCHEMA),
        'front_template': '{{front}}',
        'back_template': '{{back}}',
        'formats': [],
    },
    {
        'slug': 'ai',
        'name': 'AI',
        'description': 'AI generated prompts',
        'field_schema': list(_DEFAULT_FRONT_BACK_SCHEMA),
        'front_template': '{{front}}',
        'back_template': '{{back}}',
        'formats': [],
    },
    {
        'slug': 'photo',
        'name': 'Photo Card',
        'description': 'Question/photo/answer cards',
        'field_schema': [
            {'key': 'question', 'label': 'Question'},
            {'key': 'photo', 'label': 'Photo'},
            {'key': 'answer', 'label': 'Answer'},
        ],
        'front_template': '{{note.path}}\n{{question}}\n{{photo}}',
        'back_template': 'The photo meaning is: {{answer}}',
        'formats': [
            {
                'name': 'Photo question',
                'template': '## {{question}} #photo-card\n{{photo}}\n\n{{answer}}',
                'options': {'marker': '#photo-card', 'default_tags': ['photo-card']},
            },
            {
                'name': 'Photo quick capture',
                'template': '{{photo}} #photo-card\n\n{{answer}}',
                'options': {'marker': '#photo-card', 'default_tags': ['photo-card']},
            },
        ],
    },
]


def ensure_builtin_card_types() -> None:
    for config in BUILTIN_CARD_TYPES:
        card_type, created = CardType.objects.get_or_create(
            user=None,
            slug=config['slug'],
            defaults={
                'name': config['name'],
                'description': config.get('description', ''),
                'field_schema': config['field_schema'],
                'front_template': config['front_template'],
                'back_template': config['back_template'],
            },
        )
        updated = False
        if not created:
            for field in ('name', 'description', 'field_schema', 'front_template', 'back_template'):
                desired = config[field]
                if getattr(card_type, field) != desired:
                    setattr(card_type, field, desired)
                    updated = True
        if updated:
            card_type.save()

        existing_formats = {
            fmt.name: fmt for fmt in card_type.import_formats.filter(format_kind='markdown')
        }
        for fmt_config in config.get('formats', []):
            config_options = fmt_config.get('options', {})
            fmt = existing_formats.get(fmt_config['name'])
            if fmt:
                needs_update = False
                if fmt.template != fmt_config['template']:
                    fmt.template = fmt_config['template']
                    needs_update = True
                options = config_options
                if fmt.options != options:
                    fmt.options = options
                    needs_update = True
                if needs_update:
                    fmt.save()
                continue
            CardImportFormat.objects.create(
                card_type=card_type,
                name=fmt_config['name'],
                format_kind='markdown',
                template=fmt_config['template'],
                options=config_options,
            )
