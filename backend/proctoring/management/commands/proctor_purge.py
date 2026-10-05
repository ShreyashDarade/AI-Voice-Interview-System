from django.core.management.base import BaseCommand

from proctoring import services


class Command(BaseCommand):
    help = 'Delete expired evidence images and sessions past their retention (run daily).'

    def handle(self, *a, **o):
        self.stdout.write(str(services.purge_expired()))
