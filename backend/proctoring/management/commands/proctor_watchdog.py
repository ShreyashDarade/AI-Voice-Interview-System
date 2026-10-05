import time

from django.core.management.base import BaseCommand

from proctoring import services, webhooks


class Command(BaseCommand):
    help = 'Run every ~10s: pause silent sessions, expire abandoned ones, deliver webhooks. Use --once for cron.'

    def add_arguments(self, p):
        p.add_argument('--once', action='store_true')
        p.add_argument('--interval', type=float, default=10.0)

    def handle(self, *a, once=False, interval=10.0, **o):
        while True:
            self.stdout.write(f'{services.expire_stale()} {webhooks.deliver_pending()}')
            if once:
                return
            time.sleep(interval)
