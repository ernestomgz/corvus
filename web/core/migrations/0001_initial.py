import uuid

import django.contrib.postgres.fields
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Deck',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                (
                    'parent',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='children',
                        to='core.deck',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='decks',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['name'],
                'indexes': [
                    models.Index(fields=['user', 'parent'], name='core_deck_user_id_parent_idx'),
                    models.Index(fields=['user', 'name'], name='core_deck_user_id_name_idx'),
                    models.Index(fields=['user', 'id'], name='core_deck_user_id_id_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'parent', 'name'), name='unique_deck_per_parent'),
                ],
            },
        ),
        migrations.CreateModel(
            name='Import',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('markdown', 'Markdown'), ('anki', 'Anki')], max_length=10)),
                (
                    'status',
                    models.CharField(
                        choices=[('ok', 'OK'), ('error', 'Error'), ('partial', 'Partial')],
                        max_length=10,
                    ),
                ),
                ('summary', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='imports',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['user', 'created_at'], name='core_import_user_id_created_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='KnowledgeMap',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('slug', models.SlugField(max_length=64)),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='knowledge_maps',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['name'],
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'slug'), name='unique_knowledge_map_slug_per_user'),
                ],
            },
        ),
        migrations.CreateModel(
            name='StudySet',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('kind', models.CharField(choices=[('deck', 'Deck'), ('tag', 'Tag'), ('custom', 'Custom')], max_length=20)),
                ('tag', models.CharField(blank=True, max_length=255)),
                (
                    'tags',
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                (
                    'filenames',
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                ('is_favorite', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'deck',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='study_sets',
                        to='core.deck',
                    ),
                ),
                ('decks', models.ManyToManyField(blank=True, related_name='custom_study_sets', to='core.deck')),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='study_sets',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['-is_favorite', 'name'],
                'constraints': [
                    models.CheckConstraint(
                        condition=(
                            models.Q(('deck__isnull', False), ('kind', 'deck'), ('tag', ''))
                            | models.Q(('deck__isnull', True), ('kind', 'tag'), ('tag__gt', ''))
                            | models.Q(('deck__isnull', True), ('kind', 'custom'), ('tag', ''))
                        ),
                        name='study_set_kind_constraints',
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name='UserSettings',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('new_card_daily_limit', models.IntegerField(default=20)),
                ('notifications_enabled', models.BooleanField(default=False)),
                ('theme', models.CharField(default='system', max_length=20)),
                ('plugin_github_enabled', models.BooleanField(default=False)),
                ('plugin_github_repo', models.CharField(blank=True, max_length=255)),
                ('plugin_github_branch', models.CharField(default='update-cards-bot', max_length=255)),
                ('plugin_github_token', models.TextField(blank=True)),
                ('plugin_ai_enabled', models.BooleanField(default=False)),
                ('plugin_ai_provider', models.CharField(blank=True, max_length=50)),
                ('plugin_ai_api_key', models.TextField(blank=True)),
                (
                    'scheduled_pull_interval',
                    models.CharField(
                        choices=[('off', 'Off'), ('hourly', 'Hourly'), ('daily', 'Daily')],
                        default='off',
                        max_length=20,
                    ),
                ),
                ('max_delete_threshold', models.IntegerField(default=50)),
                ('require_recent_pull_before_push', models.BooleanField(default=True)),
                ('push_preview_required', models.BooleanField(default=True)),
                ('last_pull_at', models.DateTimeField(blank=True, null=True)),
                ('last_push_at', models.DateTimeField(blank=True, null=True)),
                ('last_sync_status', models.CharField(blank=True, max_length=32)),
                ('last_sync_error', models.TextField(blank=True)),
                ('last_sync_summary', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'default_deck',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='default_for_users',
                        to='core.deck',
                    ),
                ),
                (
                    'default_study_set',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='default_for_users',
                        to='core.studyset',
                    ),
                ),
                (
                    'user',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='settings',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['user_id'],
            },
        ),
        migrations.CreateModel(
            name='Card',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('front_md', models.TextField()),
                ('back_md', models.TextField()),
                (
                    'tags',
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                ('source_path', models.TextField(blank=True, null=True)),
                ('source_anchor', models.TextField(blank=True, null=True)),
                ('media', models.JSONField(default=list)),
                (
                    'import_id',
                    models.CharField(
                        blank=True,
                        help_text='Hexadecimal import ID',
                        max_length=16,
                        null=True,
                        unique=True,
                    ),
                ),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'deck',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='cards',
                        to='core.deck',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='cards',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['-updated_at'],
                'indexes': [
                    models.Index(fields=['user', 'deck'], name='core_card_user_id_deck_id_idx'),
                    models.Index(fields=['updated_at'], name='core_card_updated_at_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='ExternalId',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('system', models.CharField(choices=[('logseq', 'Logseq'), ('anki', 'Anki'), ('manual', 'Manual')], max_length=10)),
                ('external_key', models.TextField(unique=True)),
                ('extra', models.JSONField(default=dict)),
                (
                    'card',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='external_ids',
                        to='core.card',
                    ),
                ),
            ],
            options={
                'indexes': [
                    models.Index(fields=['system'], name='core_externalid_system_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='ImportSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('markdown', 'Markdown'), ('anki', 'Anki')], max_length=10)),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('pending', 'Pending'),
                            ('ready', 'Ready'),
                            ('applied', 'Applied'),
                            ('cancelled', 'Cancelled'),
                            ('error', 'Error'),
                        ],
                        default='pending',
                        max_length=20,
                    ),
                ),
                ('source_name', models.CharField(blank=True, max_length=255)),
                ('total', models.IntegerField(default=0)),
                ('processed', models.IntegerField(default=0)),
                ('payload', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'import_record',
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='session',
                        to='core.import',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='import_sessions',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['user', 'status'], name='core_importsession_user_status_idx'),
                    models.Index(fields=['created_at'], name='core_importsession_created_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='KnowledgeNode',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('identifier', models.CharField(max_length=96)),
                ('title', models.CharField(max_length=255)),
                ('definition', models.TextField(blank=True)),
                ('guidance', models.TextField(blank=True)),
                ('sources', models.JSONField(blank=True, default=list)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('display_order', models.IntegerField(default=0)),
                ('tag_value', models.CharField(max_length=255, unique=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'knowledge_map',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='nodes',
                        to='core.knowledgemap',
                    ),
                ),
                (
                    'parent',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='children',
                        to='core.knowledgenode',
                    ),
                ),
            ],
            options={
                'ordering': ['knowledge_map', 'display_order', 'title'],
                'indexes': [
                    models.Index(fields=['knowledge_map', 'parent'], name='core_knowledgenode_map_parent_idx'),
                    models.Index(fields=['knowledge_map', 'identifier'], name='core_knowledgenode_map_identifier_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('knowledge_map', 'identifier'),
                        name='unique_knowledge_node_identifier_per_map',
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name='Review',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('reviewed_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('rating', models.SmallIntegerField()),
                ('elapsed_days', models.IntegerField()),
                ('interval_before', models.IntegerField()),
                ('interval_after', models.IntegerField()),
                ('ease_before', models.FloatField()),
                ('ease_after', models.FloatField()),
                (
                    'card',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='reviews',
                        to='core.card',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='reviews',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['-reviewed_at'],
                'indexes': [
                    models.Index(fields=['user', 'reviewed_at'], name='core_review_user_id_reviewed_idx'),
                    models.Index(fields=['card', 'reviewed_at'], name='core_review_card_id_reviewed_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='SchedulingState',
            fields=[
                (
                    'card',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name='scheduling_state',
                        serialize=False,
                        to='core.card',
                    ),
                ),
                ('ease', models.FloatField(default=2.5)),
                ('interval_days', models.IntegerField(default=0)),
                ('reps', models.IntegerField(default=0)),
                ('lapses', models.IntegerField(default=0)),
                ('due_at', models.DateTimeField(blank=True, null=True)),
                (
                    'queue_status',
                    models.CharField(
                        choices=[('new', 'New'), ('learn', 'Learn'), ('review', 'Review'), ('relearn', 'Relearn')],
                        default='new',
                        max_length=10,
                    ),
                ),
                ('learning_step_index', models.SmallIntegerField(default=0)),
                ('last_rating', models.SmallIntegerField(blank=True, null=True)),
            ],
            options={
                'ordering': ['due_at'],
            },
        ),
    ]
