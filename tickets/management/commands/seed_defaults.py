from django.core.management.base import BaseCommand

from tickets.seed import ensure_defaults


class Command(BaseCommand):
    help = "Создаёт группы ролей и стартовые категории оборудования (безопасно запускать повторно)."

    def handle(self, *args, **options):
        ensure_defaults()
        self.stdout.write(self.style.SUCCESS("Группы и стартовые категории на месте."))
