import threading

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection, connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from tickets import services
from tickets.models import EquipmentCategory, Floor, Location, Ticket
from tickets.permissions import TECH_GROUP

User = get_user_model()


class RealConcurrencyTests(TransactionTestCase):
    """Настоящие параллельные потоки, каждый со своим соединением с БД.
    Нужен PostgreSQL (на SQLite потоки сериализуются блокировкой файла — тест не показателен)."""

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Нужен PostgreSQL (DB_ENGINE=postgres)")
        Group.objects.get_or_create(name=TECH_GROUP)
        self.techs = []
        for i in range(6):
            u = User.objects.create_user(f"t{i}", password="x")
            u.groups.add(Group.objects.get(name=TECH_GROUP))
            self.techs.append(u)
        floor = Floor.objects.create(name="1 этаж")
        room = Location.objects.create(floor=floor, name="101")
        cat, _ = EquipmentCategory.objects.get_or_create(name="Принтер")
        self.ticket = services.create_ticket(employee_name="А", location=room, location_text="", summary="x",
                                             details="", items=[(cat, 1)])

    def test_only_one_of_many_simultaneous_claims_wins(self):
        barrier = threading.Barrier(len(self.techs))
        results = []

        def worker(user):
            try:
                barrier.wait()
                services.claim_ticket(self.ticket.pk, user)
                results.append(("ok", user.pk))
            except services.TicketTaken:
                results.append(("taken", user.pk))
            except Exception as e:  # pragma: no cover
                results.append(("error", repr(e)))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(u,)) for u in self.techs]
        [t.start() for t in threads]
        [t.join() for t in threads]

        outcomes = [r[0] for r in results]
        self.assertEqual(outcomes.count("ok"), 1, results)
        self.assertEqual(outcomes.count("taken"), len(self.techs) - 1, results)
        t = Ticket.objects.get(pk=self.ticket.pk)
        winner = next(pk for kind, pk in results if kind == "ok")
        self.assertEqual(t.assignee_id, winner)
        self.assertEqual(t.history.filter(action="claimed").count(), 1)

    def test_concurrent_ticket_creation_gives_unique_numbers(self):
        floor = Floor.objects.first()
        room = Location.objects.first()
        cat = EquipmentCategory.objects.first()
        barrier = threading.Barrier(8)
        numbers, errors = [], []

        def worker():
            try:
                barrier.wait()
                t = services.create_ticket(employee_name="Б", location=room, location_text="", summary="y",
                                           details="", items=[(cat, 1)])
                numbers.append(t.number)
            except Exception as e:  # pragma: no cover
                errors.append(repr(e))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(set(numbers)), 8)
