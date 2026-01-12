from django.contrib import admin
from .models import Resume, Interview, Question, CheatingEvent


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ['id', 'candidate_name', 'email', 'experience_years', 'created_at']
    list_filter = ['experience_years', 'created_at']
    search_fields = ['candidate_name', 'email', 'skills']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(Interview)
class InterviewAdmin(admin.ModelAdmin):
    list_display = ['id', 'resume', 'status', 'experience_level', 'strikes', 'start_time']
    list_filter = ['status', 'experience_level', 'created_at']
    search_fields = ['resume__candidate_name']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ['id', 'interview', 'category', 'difficulty', 'asked_at']
    list_filter = ['category', 'difficulty']
    search_fields = ['text']


@admin.register(CheatingEvent)
class CheatingEventAdmin(admin.ModelAdmin):
    list_display = ['id', 'interview', 'event_type', 'confidence', 'resulted_in_strike', 'timestamp']
    list_filter = ['event_type', 'resulted_in_strike', 'timestamp']
