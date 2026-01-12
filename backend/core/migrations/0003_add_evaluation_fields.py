# Generated migration for adding evaluation and cheating events summary

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_add_event_types_and_indexes'),
    ]

    operations = [
        # Add cheating events summary field
        migrations.AddField(
            model_name='interview',
            name='cheating_events_summary',
            field=models.JSONField(blank=True, default=list),
        ),
        
        # Add evaluation field for AI-generated review
        migrations.AddField(
            model_name='interview',
            name='evaluation',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
