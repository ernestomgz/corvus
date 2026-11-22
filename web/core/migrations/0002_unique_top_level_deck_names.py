from django.db import migrations, models


def check_top_level_deck_names(apps, schema_editor):
    Deck = apps.get_model('core', 'Deck')
    duplicates = list(
        Deck.objects.using(schema_editor.connection.alias)
        .filter(parent__isnull=True)
        .values('user_id', 'name')
        .annotate(deck_count=models.Count('pk'))
        .filter(deck_count__gt=1)
        .order_by('user_id', 'name')[:10]
    )
    if duplicates:
        examples = ', '.join(f"user {row['user_id']}: {row['name']!r}" for row in duplicates)
        raise RuntimeError(
            'Duplicate top-level deck names prevent this migration. '
            f'Rename the duplicate decks for each affected user and retry. Examples: {examples}'
        )


class Migration(migrations.Migration):
    dependencies = [('core', '0001_initial')]

    operations = [
        migrations.RunPython(check_top_level_deck_names, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name='deck', name='unique_deck_per_parent'),
        migrations.AddConstraint(
            model_name='deck',
            constraint=models.UniqueConstraint(
                fields=('user', 'parent', 'name'), name='unique_deck_per_parent', nulls_distinct=False,
            ),
        ),
    ]
