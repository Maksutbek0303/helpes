from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from tickets import services
from tickets.models import Building, EquipmentCategory, Floor, Location
from tickets.permissions import ADMIN_GROUP, TECH_GROUP

User = get_user_model()


def make_user(username, tech=False, admin=False, name=None, active=True):
    u = User.objects.create_user(username=username, password="pass-12345-xyz", first_name=name or username.title(),
                                 is_active=active)
    if tech:
        u.groups.add(Group.objects.get(name=TECH_GROUP))
    if admin:
        u.groups.add(Group.objects.get(name=ADMIN_GROUP))
    return u


class HelpesTestCase(TestCase):
    """Группы и стартовые категории создаёт миграция 0002 — как и в рабочей базе."""

    def setUp(self):
        cache.clear()
        self.admin = make_user("boss", admin=True, name="Админ")
        self.tech1 = make_user("ivan", tech=True, name="Иван")
        self.tech2 = make_user("petr", tech=True, name="Пётр")
        self.floor = Floor.objects.create(name="2 этаж")
        self.room = Location.objects.create(floor=self.floor, name="Кабинет 201")
        self.cat_pc = EquipmentCategory.objects.get(name="Компьютер или ноутбук")
        self.cat_printer = EquipmentCategory.objects.get(name="Принтер")

    def login(self, user):
        self.client.force_login(user)
        return self.client

    def make_ticket(self, **kw):
        data = dict(employee_name="Анна", location=self.room, location_text="", summary="Не включается",
                    details="", items=[(self.cat_pc, 2)])
        data.update(kw)
        return services.create_ticket(**data)
