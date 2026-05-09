from __future__ import annotations

import uuid
from typing import Iterable

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .knowledge_tags import build_knowledge_tag


class UserScopedQuerySet(models.QuerySet):
    def for_user(self, user: settings.AUTH_USER_MODEL) -> "UserScopedQuerySet":
        return self.filter(user=user)


class Deck(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='decks')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='children')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    objects = UserScopedQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'parent', 'name'], name='unique_deck_per_parent'),
        ]
        indexes = [
            models.Index(fields=['user', 'parent']),
            models.Index(fields=['user', 'name']),
            models.Index(fields=['user', 'id']),
        ]
        ordering = ['name']

    def full_path(self) -> str:
        parts = [self.name]
        current = self.parent
        while current is not None:
            parts.append(current.name)
            current = current.parent
        return '/'.join(reversed(parts))

    def __str__(self) -> str:
        return self.full_path()

    def descendant_ids(self, include_self: bool = True) -> list[int]:
        from collections import defaultdict

        rows = Deck.objects.filter(user=self.user).values('id', 'parent_id')
        children: dict[int | None, list[int]] = defaultdict(list)
        for row in rows:
            children[row['parent_id']].append(row['id'])
        stack = [self.id]
        visited: list[int] = []
        while stack:
            current_id = stack.pop()
            visited.append(current_id)
            stack.extend(children.get(current_id, []))
        if not include_self and self.id in visited:
            visited.remove(self.id)
        return visited


class StudySet(models.Model):
    KIND_DECK = 'deck'
    KIND_TAG = 'tag'
    KIND_CUSTOM = 'custom'
    KIND_CHOICES = [
        (KIND_DECK, 'Deck'),
        (KIND_TAG, 'Tag'),
        (KIND_CUSTOM, 'Custom'),
    ]

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='study_sets')
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    deck = models.ForeignKey(Deck, null=True, blank=True, on_delete=models.CASCADE, related_name='study_sets')
    tag = models.CharField(max_length=255, blank=True)
    decks = models.ManyToManyField(Deck, blank=True, related_name='custom_study_sets')
    tags = ArrayField(models.TextField(), blank=True, default=list)
    filenames = ArrayField(models.TextField(), blank=True, default=list)
    is_favorite = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserScopedQuerySet.as_manager()

    class Meta:
        ordering = ['-is_favorite', 'name']
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(kind='deck', deck__isnull=False, tag='')
                    | models.Q(kind='tag', deck__isnull=True, tag__gt='')
                    | models.Q(kind='custom', deck__isnull=True, tag='')
                ),
                name='study_set_kind_constraints',
            ),
        ]

    def __str__(self) -> str:
        if self.kind == self.KIND_TAG:
            return f"{self.name} (Tag: {self.tag})"
        if self.deck:
            return f"{self.name} (Deck: {self.deck.full_path()})"
        if self.kind == self.KIND_CUSTOM:
            parts = []
            if self.decks.exists():
                deck_names = [d.full_path() for d in self.decks.all()]
                parts.append(f"Decks: {', '.join(deck_names)}")
            if self.tags:
                parts.append(f"Tags: {', '.join(self.tags)}")
            if self.filenames:
                parts.append(f"Files: {', '.join(self.filenames)}")
            return f"{self.name} ({'; '.join(parts)})"
        return self.name


class UserSettings(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='settings')
    default_deck = models.ForeignKey(Deck, null=True, blank=True, on_delete=models.SET_NULL, related_name='default_for_users')
    default_study_set = models.ForeignKey(
        StudySet, null=True, blank=True, on_delete=models.SET_NULL, related_name='default_for_users'
    )
    new_card_daily_limit = models.IntegerField(default=20)
    notifications_enabled = models.BooleanField(default=False)
    theme = models.CharField(max_length=20, default='system')
    plugin_github_enabled = models.BooleanField(default=False)
    plugin_github_repo = models.CharField(max_length=255, blank=True)
    plugin_github_branch = models.CharField(max_length=255, default='update-cards-bot')
    plugin_github_token = models.TextField(blank=True)
    plugin_ai_enabled = models.BooleanField(default=False)
    plugin_ai_provider = models.CharField(max_length=50, blank=True)
    plugin_ai_api_key = models.TextField(blank=True)
    scheduled_pull_interval = models.CharField(
        max_length=20,
        default='off',
        choices=[('off', 'Off'), ('hourly', 'Hourly'), ('daily', 'Daily')],
    )
    max_delete_threshold = models.IntegerField(default=50)
    require_recent_pull_before_push = models.BooleanField(default=True)
    push_preview_required = models.BooleanField(default=True)
    last_pull_at = models.DateTimeField(null=True, blank=True)
    last_push_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=32, blank=True)
    last_sync_error = models.TextField(blank=True)
    last_sync_summary = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user_id']

    def to_export_payload(self) -> dict:
        """Return a safe dict for export (excludes secrets by default)."""
        return {
            'default_deck_id': self.default_deck_id,
            'default_study_set_id': self.default_study_set_id,
            'new_card_daily_limit': self.new_card_daily_limit,
            'notifications_enabled': self.notifications_enabled,
            'theme': self.theme,
            'plugin_github': {
                'enabled': self.plugin_github_enabled,
                'repo': self.plugin_github_repo,
                'branch': self.plugin_github_branch,
            },
            'plugin_ai': {
                'enabled': self.plugin_ai_enabled,
                'provider': self.plugin_ai_provider,
            },
            'sync_policy': {
                'scheduled_pull': self.scheduled_pull_interval,
                'max_delete_threshold': self.max_delete_threshold,
                'require_recent_pull_before_push': self.require_recent_pull_before_push,
                'push_preview_required': self.push_preview_required,
            },
            'last_sync': {
                'last_pull_at': self.last_pull_at.isoformat() if self.last_pull_at else None,
                'last_push_at': self.last_push_at.isoformat() if self.last_push_at else None,
                'status': self.last_sync_status or '',
                'summary': self.last_sync_summary or {},
            },
            'metadata': self.metadata or {},
        }


class KnowledgeNodeQuerySet(models.QuerySet):
    def for_user(self, user: settings.AUTH_USER_MODEL) -> "KnowledgeNodeQuerySet":
        return self.filter(knowledge_map__user=user)


class KnowledgeMap(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='knowledge_maps',
    )
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserScopedQuerySet.as_manager()

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['user', 'slug'], name='unique_knowledge_map_slug_per_user'),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"


class KnowledgeNode(models.Model):
    id = models.BigAutoField(primary_key=True)
    knowledge_map = models.ForeignKey(
        KnowledgeMap,
        on_delete=models.CASCADE,
        related_name='nodes',
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        related_name='children',
        null=True,
        blank=True,
    )
    identifier = models.CharField(max_length=96)
    title = models.CharField(max_length=255)
    definition = models.TextField(blank=True)
    guidance = models.TextField(blank=True)
    sources = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    display_order = models.IntegerField(default=0)
    tag_value = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = KnowledgeNodeQuerySet.as_manager()

    class Meta:
        ordering = ['knowledge_map', 'display_order', 'title']
        indexes = [
            models.Index(fields=['knowledge_map', 'parent']),
            models.Index(fields=['knowledge_map', 'identifier']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['knowledge_map', 'identifier'],
                name='unique_knowledge_node_identifier_per_map',
            ),
        ]

    def __str__(self) -> str:
        return f"{self.knowledge_map.slug}:{self.identifier}"

    def clean(self) -> None:
        super().clean()
        if self.parent and self.parent.knowledge_map_id != self.knowledge_map_id:
            raise ValidationError('parent must belong to the same knowledge map')

    def save(self, *args, **kwargs) -> None:
        self.clean()
        if self.knowledge_map_id and self.identifier:
            calculated = build_knowledge_tag(self.knowledge_map.slug, self.identifier)
            if self.tag_value != calculated:
                self.tag_value = calculated
        super().save(*args, **kwargs)

    def knowledge_tag(self) -> str:
        return self.tag_value

    def full_path(self) -> str:
        segments = [self.title]
        ancestor = self.parent
        while ancestor is not None:
            segments.append(ancestor.title)
            ancestor = ancestor.parent
        return ' / '.join(reversed(segments))


class Card(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cards')
    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name='cards')
    front_md = models.TextField()
    back_md = models.TextField()
    tags = ArrayField(models.TextField(), blank=True, default=list)
    source_path = models.TextField(null=True, blank=True)
    source_anchor = models.TextField(null=True, blank=True)
    media = models.JSONField(default=list)
    import_id = models.CharField(max_length=16, unique=True, null=True, blank=True, help_text="Hexadecimal import ID")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserScopedQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=['user', 'deck']),
            models.Index(fields=['updated_at']),
        ]
        ordering = ['-updated_at']

    def __str__(self) -> str:
        return self.front_md[:40]

    def add_tag(self, tag: str) -> None:
        normalised = tag.strip()
        if not normalised:
            return
        if normalised not in self.tags:
            self.tags.append(normalised)
            self.save(update_fields=['tags'])

    def save(self, *args, **kwargs):
        # Auto-assign import_id if not set
        if self.import_id is None:
            # Find next available sequential hex ID globally
            existing_ids = set(Card.objects.filter(import_id__isnull=False).values_list('import_id', flat=True))
            next_id = 1
            while True:
                candidate = hex(next_id)[2:].lower()
                if candidate not in existing_ids:
                    self.import_id = candidate
                    break
                next_id += 1
        super().save(*args, **kwargs)


class SchedulingState(models.Model):
    QUEUE_STATUS_CHOICES = [
        ('new', 'New'),
        ('learn', 'Learn'),
        ('review', 'Review'),
        ('relearn', 'Relearn'),
    ]

    card = models.OneToOneField(Card, on_delete=models.CASCADE, primary_key=True, related_name='scheduling_state')
    ease = models.FloatField(default=2.5)
    interval_days = models.IntegerField(default=0)
    reps = models.IntegerField(default=0)
    lapses = models.IntegerField(default=0)
    due_at = models.DateTimeField(null=True, blank=True)
    queue_status = models.CharField(max_length=10, choices=QUEUE_STATUS_CHOICES, default='new')
    learning_step_index = models.SmallIntegerField(default=0)
    last_rating = models.SmallIntegerField(null=True, blank=True)

    objects = models.Manager()

    class Meta:
        ordering = ['due_at']


class Review(models.Model):
    id = models.BigAutoField(primary_key=True)
    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    reviewed_at = models.DateTimeField(default=timezone.now)
    rating = models.SmallIntegerField()
    elapsed_days = models.IntegerField()
    interval_before = models.IntegerField()
    interval_after = models.IntegerField()
    ease_before = models.FloatField()
    ease_after = models.FloatField()

    class Meta:
        indexes = [
            models.Index(fields=['user', 'reviewed_at']),
            models.Index(fields=['card', 'reviewed_at']),
        ]
        ordering = ['-reviewed_at']


class ExternalId(models.Model):
    SYSTEM_CHOICES = [
        ('logseq', 'Logseq'),
        ('anki', 'Anki'),
        ('manual', 'Manual'),
    ]

    id = models.BigAutoField(primary_key=True)
    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name='external_ids')
    system = models.CharField(max_length=10, choices=SYSTEM_CHOICES)
    external_key = models.TextField(unique=True)
    extra = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(fields=['system']),
        ]

    def __str__(self) -> str:
        return f"{self.system}:{self.external_key}"


class Import(models.Model):
    KIND_CHOICES = [
        ('markdown', 'Markdown'),
        ('anki', 'Anki'),
    ]
    STATUS_CHOICES = [
        ('ok', 'OK'),
        ('error', 'Error'),
        ('partial', 'Partial'),
    ]

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='imports')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'created_at']),
        ]
        ordering = ['-created_at']


class ImportSession(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('ready', 'Ready'),
        ('applied', 'Applied'),
        ('cancelled', 'Cancelled'),
        ('error', 'Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='import_sessions')
    kind = models.CharField(max_length=10, choices=Import.KIND_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    source_name = models.CharField(max_length=255, blank=True)
    total = models.IntegerField(default=0)
    processed = models.IntegerField(default=0)
    payload = models.JSONField(default=dict)
    import_record = models.OneToOneField('Import', null=True, blank=True, on_delete=models.SET_NULL, related_name='session')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['created_at']),
        ]
        ordering = ['-created_at']



def bulk_upsert_external_ids(external_id_tuples: Iterable[tuple[str, str, Card]]) -> None:
    """Utility to bulk-create external ids without clobbering existing rows."""
    objects = [
        ExternalId(system=system, external_key=external_key, card=card)
        for system, external_key, card in external_id_tuples
    ]
    if objects:
        ExternalId.objects.bulk_create(objects, ignore_conflicts=True)
