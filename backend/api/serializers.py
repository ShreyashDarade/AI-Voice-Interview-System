"""
Serializers for AI Interviewer API.
"""
from rest_framework import serializers
from core.models import Resume, Interview, Question


class ResumeUploadSerializer(serializers.Serializer):
    """Cheap pre-checks only; the resume engine verifies the real file type from magic bytes."""
    file = serializers.FileField()

    def validate_file(self, value):
        from django.conf import settings
        ext = value.name.rsplit('.', 1)[-1].lower() if '.' in value.name else ''
        if ext not in settings.ALLOWED_RESUME_EXTENSIONS:
            raise serializers.ValidationError("Unsupported file type. Please upload PDF, DOCX, or TXT.")
        if value.size > settings.MAX_RESUME_SIZE_MB * 1024 * 1024:
            raise serializers.ValidationError(f"File too large. Maximum size is {settings.MAX_RESUME_SIZE_MB}MB.")
        return value


class ResumeSerializer(serializers.ModelSerializer):
    """Serializer for Resume model."""
    
    class Meta:
        model = Resume
        fields = [
            'id', 'original_filename', 'candidate_name', 'email', 'phone',
            'experience_years', 'skills', 'education', 'work_history', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class ResumeDetailSerializer(serializers.ModelSerializer):
    """Everything except the raw text/file path/internal prompt (PII minimisation)."""

    class Meta:
        model = Resume
        exclude = ['raw_text', 'file', 'owner_id', 'content_sha256', 'simhash']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['parsed_data'] = {k: v for k, v in (data.get('parsed_data') or {}).items() if k != 'probe_prompt'}
        return data


class InterviewCreateSerializer(serializers.Serializer):
    """Serializer for creating an interview + proctoring session."""
    resume_id = serializers.UUIDField()
    experience_level = serializers.ChoiceField(
        choices=Interview.ExperienceLevel.choices,
        default=Interview.ExperienceLevel.FRESHER
    )
    policy = serializers.ChoiceField(choices=['lenient', 'standard', 'strict'], required=False)
    policy_overrides = serializers.DictField(required=False, default=dict)
    accommodations = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    external_ref = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    
    def validate_resume_id(self, value):
        """Validate resume exists."""
        try:
            Resume.objects.get(id=value)
        except Resume.DoesNotExist:
            raise serializers.ValidationError("Resume not found.")
        return value


class InterviewSerializer(serializers.ModelSerializer):
    """Serializer for Interview model."""
    resume = ResumeSerializer(read_only=True)
    duration_seconds = serializers.SerializerMethodField()
    
    class Meta:
        model = Interview
        fields = [
            'id', 'resume', 'experience_level', 'status', 
            'start_time', 'end_time', 'strikes', 'termination_reason',
            'duration_seconds', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
    
    def get_duration_seconds(self, obj):
        """Calculate interview duration in seconds."""
        if obj.start_time and obj.end_time:
            return (obj.end_time - obj.start_time).total_seconds()
        return None


class QuestionSerializer(serializers.ModelSerializer):
    """Serializer for Question model."""
    
    class Meta:
        model = Question
        fields = [
            'id', 'text', 'category', 'difficulty', 
            'skill_tag', 'asked_at', 'response'
        ]
        read_only_fields = ['id']


class HealthSerializer(serializers.Serializer):
    """Serializer for health check response."""
    status = serializers.CharField()
    version = serializers.CharField()
    timestamp = serializers.DateTimeField()
