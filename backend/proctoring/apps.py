from django.apps import AppConfig


class ProctoringConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'proctoring'

    def ready(self):
        # Importing the detector packages registers every built-in plugin.
        from proctoring import detectors  # noqa: F401
