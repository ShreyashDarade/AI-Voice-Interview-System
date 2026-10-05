from django.conf import settings
from django.core.management.base import BaseCommand

from proctoring.vision import assets


class Command(BaseCommand):
    help = 'Download and checksum-verify the on-device vision models (never committed to git).'

    def add_arguments(self, p):
        p.add_argument('--force', action='store_true')
        p.add_argument('--only', nargs='*', choices=sorted(assets.MODEL_SPECS))

    def handle(self, *a, force=False, only=None, **o):
        d = settings.PROCTOR['MODEL_DIR']
        for key in only or assets.MODEL_SPECS:
            spec = assets.MODEL_SPECS[key]
            try:
                path = assets.fetch(d, key, force=force)
                self.stdout.write(self.style.SUCCESS(f'ok   {spec.filename}  ({spec.purpose})'))
            except Exception as exc:
                (self.stderr if spec.required else self.stdout).write(f'FAIL {spec.filename}: {exc}')
                if spec.required:
                    raise SystemExit(1)
