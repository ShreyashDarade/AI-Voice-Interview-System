"""
Production-ready models for AI Interviewer application.
Includes indexes, constraints, and data validation.
"""
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator, FileExtensionValidator
from django.core.exceptions import ValidationError
import uuid


class Resume(models.Model):
    """Stores uploaded resume information with validation."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(
        upload_to='resumes/',
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'docx', 'txt'])]
    )
    original_filename = models.CharField(max_length=255)
    
    # Parsed content
    raw_text = models.TextField(blank=True)
    parsed_data = models.JSONField(default=dict, blank=True)
    
    # Extracted fields
    candidate_name = models.CharField(max_length=255, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    experience_years = models.FloatField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(50)]
    )
    skills = models.JSONField(default=list, blank=True)
    education = models.JSONField(default=list, blank=True)
    work_history = models.JSONField(default=list, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at'], name='resume_created_idx'),
            models.Index(fields=['candidate_name'], name='resume_name_idx'),
        ]
    
    def __str__(self):
        return f"{self.candidate_name or 'Unknown'} - {self.original_filename}"
    
    def clean(self):
        """Validate model data."""
        if self.file and self.file.size > 10 * 1024 * 1024:  # 10MB
            raise ValidationError("Resume file size must be less than 10MB")


class Interview(models.Model):
    """Represents an interview session with constraints."""
    
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED = 'completed', 'Completed'
        TERMINATED = 'terminated', 'Terminated (Cheating)'
    
    class ExperienceLevel(models.TextChoices):
        FRESHER = 'fresher', 'Fresher (0-1 years)'
        JUNIOR = 'junior', 'Junior (1-3 years)'
        MID = 'mid', 'Mid-Level (3-5 years)'
        SENIOR = 'senior', 'Senior (5-8 years)'
        LEAD = 'lead', 'Lead/Principal (8+ years)'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name='interviews',
        db_index=True
    )
    
    # Interview configuration
    experience_level = models.CharField(
        max_length=20,
        choices=ExperienceLevel.choices,
        default=ExperienceLevel.FRESHER
    )
    
    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True
    )
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    
    # Anti-cheating
    strikes = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(10)]
    )
    termination_reason = models.TextField(blank=True)
    cheating_events_summary = models.JSONField(default=list, blank=True)
    
    # Interview results
    evaluation = models.JSONField(default=dict, blank=True)  # AI-generated review
    
    # Session data
    session_data = models.JSONField(default=dict, blank=True)
    questions_asked = models.JSONField(default=list, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at'], name='interview_status_idx'),
            models.Index(fields=['resume', 'status'], name='interview_resume_status_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['resume'],
                condition=models.Q(status='in_progress'),
                name='unique_active_interview_per_resume'
            ),
        ]
    
    def __str__(self):
        return f"Interview {self.id} - {self.resume.candidate_name}"
    
    def start(self):
        """Start the interview session."""
        if self.status != self.Status.PENDING:
            raise ValidationError(f"Cannot start interview with status: {self.status}")
        self.status = self.Status.IN_PROGRESS
        self.start_time = timezone.now()
        self.save(update_fields=['status', 'start_time', 'updated_at'])
    
    def end(self, terminated=False, reason=''):
        """End the interview session."""
        if self.status not in [self.Status.IN_PROGRESS]:
            raise ValidationError(f"Cannot end interview with status: {self.status}")
        self.status = self.Status.TERMINATED if terminated else self.Status.COMPLETED
        self.end_time = timezone.now()
        if reason:
            self.termination_reason = reason
        self.save(update_fields=['status', 'end_time', 'termination_reason', 'updated_at'])
    
    def add_strike(self):
        """Add a cheating strike. Returns True if max strikes reached."""
        from django.conf import settings
        self.strikes += 1
        self.save(update_fields=['strikes', 'updated_at'])
        return self.strikes >= settings.MAX_STRIKES
    
    def get_duration_seconds(self):
        """Get interview duration in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        elif self.start_time:
            return (timezone.now() - self.start_time).total_seconds()
        return 0
    
    def clean(self):
        """Validate model data."""
        if self.end_time and self.start_time and self.end_time < self.start_time:
            raise ValidationError("End time cannot be before start time")


class Question(models.Model):
    """Interview questions with categorization."""
    
    class Category(models.TextChoices):
        TECHNICAL = 'technical', 'Technical'
        BEHAVIORAL = 'behavioral', 'Behavioral'
        SITUATIONAL = 'situational', 'Situational'
        PROJECT = 'project', 'Project-based'
    
    class Difficulty(models.TextChoices):
        EASY = 'easy', 'Easy'
        MEDIUM = 'medium', 'Medium'
        HARD = 'hard', 'Hard'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    interview = models.ForeignKey(
        Interview,
        on_delete=models.CASCADE,
        related_name='questions',
        db_index=True
    )
    
    text = models.TextField()
    category = models.CharField(max_length=20, choices=Category.choices)
    difficulty = models.CharField(max_length=10, choices=Difficulty.choices)
    skill_tag = models.CharField(max_length=100, blank=True, db_index=True)
    
    # Response tracking
    asked_at = models.DateTimeField(null=True, blank=True)
    response = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['interview', 'created_at'], name='question_interview_idx'),
        ]
    
    def __str__(self):
        return f"{self.category} - {self.text[:50]}..."


class CheatingEvent(models.Model):
    """Records cheating detection events with validation."""
    
    class EventType(models.TextChoices):
        LOOKING_AWAY = 'looking_away', 'Looking Away'
        MULTIPLE_FACES = 'multiple_faces', 'Multiple Faces'
        NO_FACE = 'no_face', 'No Face Detected'
        SUSPICIOUS_PATTERN = 'suspicious_pattern', 'Suspicious Pattern'
        TAB_SWITCH = 'tab_switch', 'Tab Switch'
        WINDOW_BLUR = 'window_blur', 'Window Blur'
        RIGHT_CLICK = 'right_click', 'Right Click'
        COPY_ATTEMPT = 'copy_attempt', 'Copy Attempt'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    interview = models.ForeignKey(
        Interview,
        on_delete=models.CASCADE,
        related_name='cheating_events',
        db_index=True
    )
    
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    confidence = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    details = models.JSONField(default=dict, blank=True)
    
    # Strike info
    resulted_in_strike = models.BooleanField(default=False)
    strike_number = models.IntegerField(null=True, blank=True)
    
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['interview', '-timestamp'], name='cheating_interview_idx'),
            models.Index(fields=['event_type', '-timestamp'], name='cheating_type_idx'),
        ]
    
    def __str__(self):
        return f"{self.event_type} at {self.timestamp}"
    
    def clean(self):
        """Validate model data."""
        if self.resulted_in_strike and self.strike_number is None:
            raise ValidationError("Strike number must be set when resulted_in_strike is True")
