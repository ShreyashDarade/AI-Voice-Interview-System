from django.core.management.base import BaseCommand, CommandError

from proctoring.framework.policy import PRESETS
from proctoring.models import Tenant
from proctoring import webhooks


class Command(BaseCommand):
    help = 'Create an integrator tenant and print its API key (shown once).'

    def add_arguments(self, p):
        p.add_argument('name')
        p.add_argument('--policy', default='standard', choices=sorted(PRESETS))
        p.add_argument('--webhook-url', default='')

    def handle(self, *a, name, policy, webhook_url, **o):
        if webhook_url:
            ok, err = webhooks.is_safe_url(webhook_url)
            if not ok:
                raise CommandError(err)
        t, key = Tenant.create_with_key(name, default_policy=policy, webhook_url=webhook_url)
        self.stdout.write(f'tenant   {t.id}\napi key  {key}\nwebhook secret  {t.webhook_secret}')
