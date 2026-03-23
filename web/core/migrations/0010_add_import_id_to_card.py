# Generated manually for adding import_id to Card model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_usersettings_backfill_sync_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='card',
            name='import_id',
            field=models.CharField(blank=True, help_text='Hexadecimal import ID', max_length=16, null=True, unique=True),
        ),
    ]