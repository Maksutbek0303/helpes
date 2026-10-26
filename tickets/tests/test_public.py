import re
from unittest import mock

from django.db import DatabaseError
from django.test import override_settings
from django.urls import reverse

from tickets.models import Building, EquipmentCategory, Floor, Location, Ticket, TicketItem

from .base import HelpesTestCase


def payload(room, items=((1, 3),), **extra):
    data = {"employee_name": "Анна Иванова", "location": str(room.pk) if room else "", "location_text": "",
            "summary": "Не включается", "details": "Начало утром", "website": ""}
    data["items-TOTAL_FORMS"] = str(len(items))
    data["items-INITIAL_FORMS"] = "0"
    data["items-MIN_NUM_FORMS"] = "1"
    data["items-MAX_NUM_FORMS"] = "20"
    for i, (cat, qty) in enumerate(items):
        data[f"items-{i}-category"] = str(cat)
        data[f"items-{i}-quantity"] = str(qty)
    data.update(extra)
    return data


class PublicFormTests(HelpesTestCase):
    def post(self, **kw):
        return self.client.post(reverse("new_ticket"), payload(self.room, **kw))

    # 1
    def test_anonymous_can_open_form(self):
        r = self.client.get(reverse("new_ticket"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Отправить заявку")
        self.assertContains(r, 'name="viewport"')  # адаптивность: мета-тег для телефонов

    # 1-4
    def test_create_valid_ticket_with_several_items(self):
        r = self.post(items=((self.cat_pc.pk, 3), (self.cat_printer.pk, 1)))
        t = Ticket.objects.get()
        self.assertRedirects(r, reverse("ticket_created", args=[t.number]))
        self.assertRegex(t.number, r"^HELP-\d{6}$")
        self.assertEqual(t.employee_name, "Анна Иванова")
        self.assertEqual(t.location, self.room)
        self.assertIn("Кабинет 201", t.place)
        self.assertEqual(t.status, "new")
        self.assertIsNone(t.assignee)
        self.assertEqual(sorted(t.items.values_list("category__name", "quantity")),
                         sorted([("Компьютер или ноутбук", 3), ("Принтер", 1)]))
        self.assertEqual(t.history.get().action, "created")
        self.assertContains(self.client.get(reverse("ticket_created", args=[t.number])), t.number)

    # 2
    def test_numbers_unique_and_sequential_format(self):
        a, b = self.make_ticket(), self.make_ticket()
        self.assertNotEqual(a.number, b.number)
        self.assertEqual(a.number, f"HELP-{a.pk:06d}")

    # 5
    def test_bad_quantities_rejected_on_server(self):
        for bad in ("0", "-2", "abc", "", "1.5", "100000"):
            r = self.post(items=((self.cat_pc.pk, bad),))
            self.assertEqual(r.status_code, 200, bad)
            self.assertEqual(Ticket.objects.count(), 0, bad)

    def test_db_constraint_blocks_zero_quantity(self):
        from django.db import IntegrityError, transaction
        t = self.make_ticket()
        with self.assertRaises(IntegrityError), transaction.atomic():
            TicketItem.objects.create(ticket=t, category=self.cat_pc, quantity=0)

    def test_duplicate_category_rejected(self):
        r = self.post(items=((self.cat_pc.pk, 1), (self.cat_pc.pk, 2)))
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertContains(r, "указана дважды")

    def test_inactive_or_unknown_category_rejected(self):
        self.cat_pc.is_active = False
        self.cat_pc.save()
        self.post(items=((self.cat_pc.pk, 1),))
        self.assertEqual(Ticket.objects.count(), 0)
        self.post(items=((99999, 1),))
        self.assertEqual(Ticket.objects.count(), 0)

    def test_requires_at_least_one_item(self):
        r = self.client.post(reverse("new_ticket"), payload(self.room, items=()))
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertEqual(r.status_code, 200)

    def test_free_text_location_when_room_not_listed(self):
        self.post_data = payload(None, items=((self.cat_pc.pk, 1),), location="other",
                                 location_text="Склад за углом")
        self.client.post(reverse("new_ticket"), self.post_data)
        t = Ticket.objects.get()
        self.assertIsNone(t.location)
        self.assertEqual(t.place, "Склад за углом")

    def test_location_required_one_way_or_another(self):
        r = self.client.post(reverse("new_ticket"), payload(None, items=((self.cat_pc.pk, 1),)))
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertContains(r, "Выберите кабинет")

    def test_hidden_room_cannot_be_submitted(self):
        self.room.is_active = False
        self.room.save()
        self.post(items=((self.cat_pc.pk, 1),))
        self.assertEqual(Ticket.objects.count(), 0)

    # 14
    def test_validation_errors_visible_and_input_preserved(self):
        r = self.post(items=((self.cat_pc.pk, 0),), employee_name="Сохранить Меня", summary="Мой текст")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Сохранить Меня")
        self.assertContains(r, "Мой текст")
        self.assertContains(r, "Количество должно быть не меньше 1")
        self.assertContains(r, f'<option value="{self.room.pk}" selected>')

    def test_missing_name_shows_error(self):
        r = self.post(items=((self.cat_pc.pk, 1),), employee_name="")
        self.assertContains(r, "Обязательное поле")
        self.assertEqual(Ticket.objects.count(), 0)

    def test_save_failure_is_reported_and_not_claimed_as_created(self):
        with mock.patch("tickets.views_public.services.create_ticket", side_effect=DatabaseError("boom")):
            r = self.post(items=((self.cat_pc.pk, 1),), employee_name="Не потерять")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Заявка НЕ создана")
        self.assertContains(r, "Не потерять")
        self.assertNotContains(r, "Заявка отправлена")
        self.assertEqual(Ticket.objects.count(), 0)

    def test_honeypot_blocks_bots(self):
        self.post(items=((self.cat_pc.pk, 1),), website="http://spam")
        self.assertEqual(Ticket.objects.count(), 0)

    @override_settings(HELPES_RATE_LIMIT_COUNT=2)
    def test_rate_limit(self):
        for _ in range(2):
            self.post(items=((self.cat_pc.pk, 1),))
        r = self.post(items=((self.cat_pc.pk, 1),))
        self.assertEqual(Ticket.objects.count(), 2)
        self.assertContains(r, "Слишком много заявок")

    def test_csrf_enforced_for_public_form(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        r = c.post(reverse("new_ticket"), payload(self.room, items=((self.cat_pc.pk, 1),)))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_single_building_not_forced_and_multiple_grouped(self):
        r = self.client.get(reverse("new_ticket"))
        self.assertContains(r, '<optgroup label="2 этаж">')  # здание не используется — оно не требуется
        b = Building.objects.create(name="Главный офис")
        f = Floor.objects.create(building=b, name="1 этаж")
        Location.objects.create(floor=f, name="Бухгалтерия")
        r = self.client.get(reverse("new_ticket"))
        self.assertContains(r, 'label="Главный офис, 1 этаж"')

    def test_rename_room_does_not_change_old_ticket_place(self):
        t = self.make_ticket()
        self.room.name = "Новое имя"
        self.room.save()
        t.refresh_from_db()
        self.assertIn("Кабинет 201", t.place)


class ProxyIpTests(HelpesTestCase):
    @override_settings(HELPES_RATE_LIMIT_COUNT=1, HELPES_BEHIND_PROXY=True)
    def test_rate_limit_uses_last_forwarded_address_and_ignores_spoofed_first(self):
        def send(xff):
            return self.client.post(reverse("new_ticket"), payload(self.room, items=((self.cat_pc.pk, 1),)),
                                    HTTP_X_FORWARDED_FOR=xff)
        send("1.1.1.1, 10.0.0.5")
        r = send("2.2.2.2, 10.0.0.5")  # подделан первый адрес, реальный (последний) тот же
        self.assertContains(r, "Слишком много заявок")
        send("2.2.2.2, 10.0.0.6")  # другой реальный клиент
        self.assertEqual(Ticket.objects.count(), 2)
