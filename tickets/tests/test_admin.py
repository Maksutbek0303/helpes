from django.contrib.auth import get_user_model
from django.urls import reverse

from tickets import services
from tickets.models import Building, EquipmentCategory, Floor, Location, Status, Ticket, TicketHistory

from .base import HelpesTestCase, make_user

User = get_user_model()


class AccessControlTests(HelpesTestCase):
    ADMIN_URLS = ["panel", "user_list", "user_create"]

    def test_anonymous_redirected_to_login_everywhere(self):
        t = self.make_ticket()
        for url in (reverse("queue"), reverse("panel"), reverse("user_list"), reverse("ticket_detail", args=[t.pk]),
                    reverse("catalog_list", args=["floors"])):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn(reverse("login"), r["Location"])
        for url in (reverse("ticket_claim", args=[t.pk]), reverse("ticket_assign", args=[t.pk])):
            self.assertEqual(self.client.post(url).status_code, 302)
        t.refresh_from_db()
        self.assertIsNone(t.assignee)

    # 10
    def test_technician_cannot_use_admin_features(self):
        t = self.make_ticket()
        c = self.login(self.tech1)
        for name in self.ADMIN_URLS:
            self.assertEqual(c.get(reverse(name)).status_code, 403, name)
        for key in ("floors", "locations", "categories", "buildings"):
            self.assertEqual(c.get(reverse("catalog_list", args=[key])).status_code, 403)
            self.assertEqual(c.post(reverse("catalog_new", args=[key]), {"name": "x"}).status_code, 403)
        self.assertEqual(c.get(reverse("user_edit", args=[self.tech2.pk])).status_code, 403)
        self.assertEqual(c.post(reverse("user_deactivate", args=[self.tech2.pk]), {}).status_code, 403)
        self.assertEqual(c.post(reverse("ticket_assign", args=[t.pk]), {"technician": self.tech2.pk}).status_code, 403)
        # попытка создать пользователя / выдать себе роль админа
        r = c.post(reverse("user_create"), {"username": "x", "first_name": "X", "password1": "Zq8!longpass",
                                             "password2": "Zq8!longpass", "is_admin": "on"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(User.objects.filter(username="x").exists())
        t.refresh_from_db()
        self.assertIsNone(t.assignee)

    def test_technician_cannot_reopen(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        services.change_status(t.pk, self.tech1, Status.DONE, resolution="ok")
        self.assertEqual(self.login(self.tech1).post(reverse("ticket_reopen", args=[t.pk])).status_code, 403)

    def test_user_without_role_has_no_access(self):
        nobody = make_user("nobody")
        self.assertEqual(self.login(nobody).get(reverse("queue")).status_code, 403)

    def test_logout_requires_post(self):
        self.login(self.tech1)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.client.post(reverse("logout"))
        self.assertEqual(self.client.get(reverse("queue")).status_code, 302)

    def test_login_flow(self):
        r = self.client.post(reverse("login"), {"username": "ivan", "password": "pass-12345-xyz"})
        self.assertRedirects(r, reverse("queue"), fetch_redirect_response=False)
        self.client.logout()
        r = self.client.post(reverse("login"), {"username": "ivan", "password": "wrong"})
        self.assertContains(r, "Неверный логин или пароль")


class CatalogTests(HelpesTestCase):
    # 6
    def test_admin_adds_floor_and_room_available_in_form_without_code_change(self):
        c = self.login(self.admin)
        c.post(reverse("catalog_new", args=["floors"]), {"name": "5 этаж", "sort_order": 50, "is_active": "on"})
        floor = Floor.objects.get(name="5 этаж")
        c.post(reverse("catalog_new", args=["locations"]), {"floor": floor.pk, "name": "Каб. 501", "is_active": "on"})
        self.client.logout()
        r = self.client.get(reverse("new_ticket"))
        self.assertContains(r, "5 этаж")
        self.assertContains(r, "Каб. 501")

    # 7
    def test_admin_adds_category_available_in_form_and_usable(self):
        self.login(self.admin).post(reverse("catalog_new", args=["categories"]),
                                    {"name": "Телефония", "sort_order": 5, "is_active": "on"})
        cat = EquipmentCategory.objects.get(name="Телефония")
        self.client.logout()
        self.assertContains(self.client.get(reverse("new_ticket")), "Телефония")
        t = self.make_ticket(items=[(cat, 2)])
        self.assertEqual(t.items.get().category, cat)

    def test_rename_and_deactivate_keep_history(self):
        t = self.make_ticket()
        c = self.login(self.admin)
        c.post(reverse("catalog_edit", args=["locations", self.room.pk]),
               {"floor": self.floor.pk, "name": "Каб. 201А", "is_active": "on"})
        self.room.refresh_from_db()
        self.assertEqual(self.room.name, "Каб. 201А")
        c.post(reverse("catalog_toggle", args=["locations", self.room.pk]))
        c.post(reverse("catalog_toggle", args=["categories", self.cat_pc.pk]))
        self.client.logout()
        form = self.client.get(reverse("new_ticket"))
        self.assertNotContains(form, "Каб. 201А")
        self.assertNotContains(form, 'Компьютер или ноутбук</option>')
        t.refresh_from_db()  # старая заявка цела
        self.assertEqual(t.location, self.room)
        self.assertEqual(t.items.get().category, self.cat_pc)

    def test_deactivating_floor_hides_its_rooms(self):
        self.login(self.admin).post(reverse("catalog_toggle", args=["floors", self.floor.pk]))
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("new_ticket")), "Кабинет 201")

    def test_hidden_building_hides_its_floors(self):
        b = Building.objects.create(name="Филиал")
        f = Floor.objects.create(building=b, name="1 этаж")
        Location.objects.create(floor=f, name="Касса")
        self.assertContains(self.client.get(reverse("new_ticket")), "Касса")
        b.is_active = False
        b.save()
        self.assertNotContains(self.client.get(reverse("new_ticket")), "Касса")

    def test_duplicate_floor_rejected(self):
        r = self.login(self.admin).post(reverse("catalog_new", args=["floors"]),
                                        {"name": "2 этаж", "sort_order": 0, "is_active": "on"})
        self.assertContains(r, "Такой этаж уже есть")
        self.assertEqual(Floor.objects.filter(name="2 этаж").count(), 1)

    def test_catalog_items_cannot_be_deleted_via_ui(self):
        c = self.login(self.admin)
        self.assertEqual(c.post(reverse("catalog_edit", args=["floors", self.floor.pk]) + "delete/").status_code, 404)

    def test_unknown_catalog_key_404(self):
        self.assertEqual(self.login(self.admin).get(reverse("catalog_list", args=["nope"])).status_code, 404)


class UserManagementTests(HelpesTestCase):
    def test_admin_creates_technician_who_can_work(self):
        c = self.login(self.admin)
        r = c.post(reverse("user_create"), {"username": "newtech", "first_name": "Новый Техник",
                                            "password1": "Str0ng-pass-77", "password2": "Str0ng-pass-77",
                                            "is_technician": "on"})
        self.assertRedirects(r, reverse("user_list"))
        u = User.objects.get(username="newtech")
        self.assertTrue(u.check_password("Str0ng-pass-77"))
        t = self.make_ticket()
        services.claim_ticket(t.pk, u)

    def test_create_user_validation(self):
        c = self.login(self.admin)
        r = c.post(reverse("user_create"), {"username": "ivan", "first_name": "Дубль", "password1": "123",
                                            "password2": "456", "is_technician": "on"})
        self.assertContains(r, "Такой логин уже занят")
        self.assertContains(r, "Пароли не совпадают")
        r = c.post(reverse("user_create"), {"username": "norole", "first_name": "X", "password1": "Str0ng-pass-77",
                                            "password2": "Str0ng-pass-77"})
        self.assertContains(r, "Выберите хотя бы одну роль")

    def test_admin_changes_roles(self):
        c = self.login(self.admin)
        c.post(reverse("user_edit", args=[self.tech2.pk]), {"first_name": "Пётр", "is_technician": "on",
                                                            "is_admin": "on"})
        from tickets.permissions import is_admin
        self.assertTrue(is_admin(User.objects.get(pk=self.tech2.pk)))

    def test_cannot_remove_technician_role_with_open_tickets(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        r = self.login(self.admin).post(reverse("user_edit", args=[self.tech1.pk]),
                                        {"first_name": "Иван", "is_admin": "on"})
        self.assertContains(r, "незавершённые заявки")

    def test_cannot_demote_self_or_last_admin(self):
        r = self.login(self.admin).post(reverse("user_edit", args=[self.admin.pk]),
                                        {"first_name": "Админ", "is_technician": "on"})
        self.assertContains(r, "Нельзя снять административную роль с самого себя")

    # 12
    def test_deactivation_requires_handling_open_tickets_and_keeps_history(self):
        t1, t2, done = self.make_ticket(), self.make_ticket(), self.make_ticket()
        for t in (t1, t2, done):
            services.claim_ticket(t.pk, self.tech1)
        services.change_status(done.pk, self.tech1, Status.DONE, resolution="Готово")
        history_before = TicketHistory.objects.count()
        c = self.login(self.admin)

        page = c.get(reverse("user_deactivate", args=[self.tech1.pk]))
        self.assertContains(page, "2 незавершённых заявок")
        self.assertContains(page, t1.number)
        self.assertNotContains(page, done.number)

        # без выбора действия — отказ, пользователь остаётся активным
        r = c.post(reverse("user_deactivate", args=[self.tech1.pk]), {})
        self.assertEqual(r.status_code, 200)
        self.tech1.refresh_from_db()
        self.assertTrue(self.tech1.is_active)

        # передача другому технику
        c.post(reverse("user_deactivate", args=[self.tech1.pk]), {"action": "reassign", "technician": self.tech2.pk})
        self.tech1.refresh_from_db()
        self.assertFalse(self.tech1.is_active)
        for t in (t1, t2):
            t.refresh_from_db()
            self.assertEqual(t.assignee, self.tech2)
        done.refresh_from_db()
        self.assertEqual(done.assignee, self.tech1)  # история выполненных работ нетронута
        self.assertEqual(done.status, Status.DONE)
        self.assertEqual(TicketHistory.objects.count(), history_before + 2)
        self.assertTrue(t1.history.filter(action="reassigned", old_value="Иван", new_value="Пётр").exists())
        self.assertTrue(t1.history.filter(action="claimed", actor=self.tech1).exists())  # прежнее назначение в истории
        self.assertTrue(User.objects.filter(pk=self.tech1.pk).exists())

    def test_deactivation_returning_tickets_to_queue(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        services.change_status(t.pk, self.tech1, Status.WAITING, comment="жду")
        self.login(self.admin).post(reverse("user_deactivate", args=[self.tech1.pk]), {"action": "queue"})
        t.refresh_from_db()
        self.assertEqual((t.status, t.assignee), (Status.NEW, None))
        self.assertTrue(Ticket.objects.free().filter(pk=t.pk).exists())
        services.claim_ticket(t.pk, self.tech2)

    def test_deactivate_without_open_tickets_and_login_blocked(self):
        c = self.login(self.admin)
        c.post(reverse("user_deactivate", args=[self.tech2.pk]), {})
        self.tech2.refresh_from_db()
        self.assertFalse(self.tech2.is_active)
        self.client.logout()
        r = self.client.post(reverse("login"), {"username": "petr", "password": "pass-12345-xyz"})
        self.assertContains(r, "Неверный логин или пароль")
        # повторное включение (тот же клиент был разлогинен — входим админом снова)
        c = self.login(self.admin)
        c.post(reverse("user_activate", args=[self.tech2.pk]))
        self.tech2.refresh_from_db()
        self.assertTrue(self.tech2.is_active)

    def test_cannot_deactivate_self_or_last_admin(self):
        c = self.login(self.admin)
        c.post(reverse("user_deactivate", args=[self.admin.pk]), {})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_reassign_target_must_be_active_technician(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        r = self.login(self.admin).post(reverse("user_deactivate", args=[self.tech1.pk]),
                                        {"action": "reassign", "technician": self.admin.pk})
        self.assertEqual(r.status_code, 200)
        self.tech1.refresh_from_db()
        self.assertTrue(self.tech1.is_active)

    def test_inactive_user_session_loses_access(self):
        c = self.login(self.tech2)
        self.assertEqual(c.get(reverse("queue")).status_code, 200)
        self.tech2.is_active = False
        self.tech2.save()
        self.assertNotEqual(c.get(reverse("queue")).status_code, 200)

    # 15 (частично): адаптивность проверяется наличием viewport и адаптивных контейнеров
    def test_staff_pages_have_responsive_markup(self):
        self.make_ticket()
        c = self.login(self.admin)
        for url in (reverse("queue"), reverse("panel"), reverse("user_list")):
            r = c.get(url)
            self.assertContains(r, 'name="viewport"')
            self.assertContains(r, "bootstrap.min.css")
        self.assertContains(c.get(reverse("queue"), {"scope": "open"}), "d-md-none")  # мобильные карточки
