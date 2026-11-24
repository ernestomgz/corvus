from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_questions(apps, schema_editor):
    Card = apps.get_model('core', 'Card')
    Deck = apps.get_model('core', 'Deck')
    NoteSource = apps.get_model('core', 'NoteSource')
    StudySet = apps.get_model('core', 'StudySet')
    alias = schema_editor.connection.alias
    decks = {pk: (user, parent) for pk, user, parent in Deck.objects.using(alias).values_list('pk', 'user_id', 'parent_id')}
    preset_roots = set(StudySet.objects.using(alias).exclude(source_root_deck_id=None).values_list('source_root_deck_id', flat=True))

    roots_by_deck = {}
    for deck_id, (user_id, _) in decks.items():
        roots = []
        current = deck_id
        seen = set()
        while current in decks and current not in seen:
            owner, parent = decks[current]
            if owner != user_id:
                break
            if parent is None or current in preset_roots:
                roots.append(current)
            seen.add(current)
            current = parent
        roots_by_deck[deck_id] = roots

    batch = []
    sources = Card.objects.using(alias).exclude(source_path=None).exclude(source_path='').order_by().values_list(
        'user_id', 'deck_id', 'source_path',
    ).distinct()
    for user_id, deck_id, path in sources.iterator():
        for root_id in roots_by_deck.get(deck_id, []):
            if decks[root_id][0] == user_id:
                batch.append(NoteSource(user_id=user_id, source_root_deck_id=root_id, source_path=path))
        if len(batch) >= 1000:
            NoteSource.objects.using(alias).bulk_create(batch, ignore_conflicts=True)
            batch.clear()
    NoteSource.objects.using(alias).bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [('core', '0002_unique_top_level_deck_names')]

    operations = [
        migrations.CreateModel(
            name='NoteSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_path', models.TextField()),
                ('note_type', models.CharField(default='questions', max_length=64)),
                ('type_priority', models.PositiveSmallIntegerField(default=0)),
                ('source_root_deck', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.deck')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={'constraints': [models.UniqueConstraint(
                fields=('user', 'source_root_deck', 'source_path'), name='unique_note_source_per_root',
            )]},
        ),
        migrations.RunPython(backfill_questions, migrations.RunPython.noop),
    ]
