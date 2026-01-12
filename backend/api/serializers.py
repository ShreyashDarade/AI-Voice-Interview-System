"""
Serializers for AI Interviewer API.
"""
from rest_framework import serializers
from core.models import Resume, Interview, Question, CheatingEvent


class ResumeUploadSerializer(serializers.Serializer):
    """Serializer for resume upload."""
    file = serializers.FileField()
    
    def validate_file(self, value):
        """Validate file type and size."""
        allowed_types = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/plain']
        max_size = 10 * 1024 * 1024  # 10MB
        
        if value.content_type not in allowed_types:
            raise serializers.ValidationError(
                "Unsupported file type. Please upload PDF, DOCX, or TXT."
            )
        
        if value.size > max_size:
            raise serializers.ValidationError(
                "File too large. Maximum size is 10MB."
            )
        
        return value


class ResumeSerializer(serializers.ModelSerializer):
    """Serializer for Resume model."""
    
    class Meta:
        model = Resume
        fields = [
            'id', 'original_filename', 'candidate_name', 'email', 'phone',
            'experience_years', 'skills', 'education', 'work_history',
            'parsed_data', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class ResumeDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for Resume with all fields."""
    
    class Meta:
        model = Resume
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class InterviewCreateSerializer(serializers.Serializer):
    """Serializer for starting an interview."""
    resume_id = serializers.UUIDField()
    experience_level = serializers.ChoiceField(
        choices=Interview.ExperienceLevel.choices,
        default=Interview.ExperienceLevel.FRESHER
    )
    
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


class CheatingEventSerializer(serializers.ModelSerializer):
    """Serializer for CheatingEvent model."""
    
    class Meta:
        model = CheatingEvent
        fields = [
            'id', 'event_type', 'confidence', 'details',
            'resulted_in_strike', 'strike_number', 'timestamp'
        ]
        read_only_fields = ['id', 'timestamp']


class CheatingReportSerializer(serializers.Serializer):
    """Serializer for reporting cheating detection."""
    interview_id = serializers.UUIDField()
    event_type = serializers.ChoiceField(choices=CheatingEvent.EventType.choices)
    confidence = serializers.FloatField(min_value=0.0, max_value=1.0)
    details = serializers.JSONField(required=False, default=dict)
    
    def validate_interview_id(self, value):
        """Validate interview exists and is in progress."""
        try:
            interview = Interview.objects.get(id=value)
            if interview.status != Interview.Status.IN_PROGRESS:
                raise serializers.ValidationError("Interview is not in progress.")
        except Interview.DoesNotExist:
            raise serializers.ValidationError("Interview not found.")
        return value


class HealthSerializer(serializers.Serializer):
    """Serializer for health check response."""
    status = serializers.CharField()
    version = serializers.CharField()
    timestamp = serializers.DateTimeField()
