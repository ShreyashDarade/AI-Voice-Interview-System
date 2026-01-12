# Generated migration for production readiness improvements

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        # Add new event types
        migrations.AlterField(
            model_name='cheatingevent',
            name='event_type',
            field=models.CharField(
                max_length=30,
                choices=[
                    ('looking_away', 'Looking Away'),
                    ('multiple_faces', 'Multiple Faces'),
                    ('no_face', 'No Face Detected'),
                    ('suspicious_pattern', 'Suspicious Pattern'),
                    ('tab_switch', 'Tab Switch'),
                    ('window_blur', 'Window Blur'),
                    ('right_click', 'Right Click'),
                    ('copy_attempt', 'Copy Attempt'),
                ]
            ),
        ),
        
        # Add database indexes for performance
        migrations.AddIndex(
            model_name='interview',
            index=models.Index(fields=['status', '-created_at'], name='interview_status_idx'),
        ),
        migrations.AddIndex(
            model_name='interview',
            index=models.Index(fields=['resume', 'status'], name='interview_resume_status_idx'),
        ),
        migrations.AddIndex(
            model_name='cheatingevent',
            index=models.Index(fields=['interview', '-timestamp'], name='cheating_interview_idx'),
        ),
        migrations.AddIndex(
            model_name='resume',
            index=models.Index(fields=['-created_at'], name='resume_created_idx'),
        ),
        
        # Add unique constraint to prevent duplicate active interviews
        migrations.AddConstraint(
            model_name='interview',
            constraint=models.UniqueConstraint(
                fields=['resume'],
                condition=models.Q(status='in_progress'),
                name='unique_active_interview_per_resume'
            ),
        ),
    ]
