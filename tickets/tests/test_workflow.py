from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from tickets import services
from tickets.models import Status, Ticket, TicketComment, TicketHistory

from .base import HelpesTestCase, make_user


class ClaimTests(HelpesTestCase):
    # 8
    def test_technician_claims_free_ticket(self):
        t = self.make_ticket()
        self.login(self.tech1).post(reverse("ticket_claim", args=[t.pk]))
        t.refresh_from_db()
        self.assertEqual(t.assignee, self.tech1)
        self.assertEqual(t.status, Status.IN_PROGRESS)
        h = t.history.get(action="claimed")
        self.assertEqual(h.actor, self.tech1)

    # 9
    def test_second_technician_cannot_claim_taken_ticket(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        with self.assertRaises(services.TicketTaken):
            services.claim_ticket(t.pk, self.tech2)
        t.refresh_from_db()
        self.assertEqual(t.assignee, self.tech1)
        self.assertEqual(t.history.filter(action="claimed").count(), 1)

    def test_stale_page_race_second_click_is_rejected_via_view(self):
        """Оба техника открыли заявку как свободную; второй нажимает «Взять» позже — получает отказ."""
        t = self.make_ticket()
        c1, c2 = self.client_class(), self.client_class()
        c1.force_login(self.tech1), c2.force_login(self.tech2)
        self.assertContains(c1.get(reverse("ticket_detail", args=[t.pk])), "Взять в работу")
        self.assertContains(c2.get(reverse("ticket_detail", args=[t.pk])), "Взять в работу")
        c1.post(reverse("ticket_claim", args=[t.pk]))
        r = c2.post(reverse("ticket_claim", args=[t.pk]), follow=True)
        self.assertContains(r, "Заявку уже взял другой специалист")
        t.refresh_from_db()
        self.assertEqual(t.assignee, self.tech1)

    def test_cannot_claim_done_ticket_or_as_non_technician(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        services.change_status(t.pk, self.tech1, Status.DONE, resolution="Починил")
        with self.assertRaises(services.TicketTaken):
            services.claim_ticket(t.pk, self.tech2)
        t2 = self.make_ticket()
        with self.assertRaises(services.ServiceError):
            services.claim_ticket(t2.pk, self.admin)  # только администратор, не техник

    def test_status_and_assignee_are_independent(self):
        t = self.make_ticket()
        self.assertEqual((t.status, t.assignee), (Status.NEW, None))
        services.assign_ticket(t.pk, self.admin, self.tech1)
        t.refresh_from_db()
        self.assertEqual((t.status, t.assignee), (Status.NEW, self.tech1))


class StatusFlowTests(HelpesTestCase):
    def setUp(self):
        super().setUp()
        self.t = self.make_ticket()
        services.claim_ticket(self.t.pk, self.tech1)

    # 13
    def test_status_changes_are_written_to_history(self):
        services.change_status(self.t.pk, self.tech1, Status.WAITING, comment="Жду картридж")
        services.change_status(self.t.pk, self.tech1, Status.IN_PROGRESS)
        services.change_status(self.t.pk, self.tech1, Status.DONE, resolution="Заменил картридж")
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, Status.DONE)
        self.assertEqual(self.t.resolution, "Заменил картридж")
        self.assertIsNotNone(self.t.completed_at)
        pairs = list(self.t.history.filter(action="status").values_list("old_value", "new_value"))
        self.assertEqual(pairs, [("В работе", "Ожидает"), ("Ожидает", "В работе"), ("В работе", "Выполнена")])
        self.assertTrue(self.t.history.filter(action="completed", note="Заменил картридж").exists())
        self.assertEqual(TicketComment.objects.get(ticket=self.t).text, "Жду картридж")

    def test_waiting_needs_comment_and_done_needs_resolution(self):
        with self.assertRaises(services.ServiceError):
            services.change_status(self.t.pk, self.tech1, Status.WAITING)
        with self.assertRaises(services.ServiceError):
            services.change_status(self.t.pk, self.tech1, Status.DONE, comment="готово")
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, Status.IN_PROGRESS)

    def test_other_technician_cannot_change_status_but_admin_can(self):
        with self.assertRaises(services.ServiceError):
            services.change_status(self.t.pk, self.tech2, Status.WAITING, comment="x")
        services.change_status(self.t.pk, self.admin, Status.WAITING, comment="Админ перевёл")
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, Status.WAITING)

    def test_status_via_view_and_unassigned_cannot_change(self):
        c = self.login(self.tech1)
        c.post(reverse("ticket_status", args=[self.t.pk]),
               {"status": "waiting", "comment": "Нужен пароль", "resolution": ""})
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, Status.WAITING)
        free = self.make_ticket()
        with self.assertRaises(services.ServiceError):
            services.change_status(free.pk, self.tech1, Status.IN_PROGRESS)

    def test_assignee_can_move_new_ticket_to_in_progress(self):
        t = self.make_ticket()
        services.assign_ticket(t.pk, self.admin, self.tech2)
        services.change_status(t.pk, self.tech2, Status.IN_PROGRESS)
        t.refresh_from_db()
        self.assertEqual(t.status, Status.IN_PROGRESS)

    def test_release_returns_to_queue(self):
        with self.assertRaises(services.ServiceError):
            services.release_ticket(self.t.pk, self.tech2)  # чужую вернуть нельзя
        services.release_ticket(self.t.pk, self.tech1)
        self.t.refresh_from_db()
        self.assertEqual((self.t.status, self.t.assignee), (Status.NEW, None))
        self.assertTrue(self.t.history.filter(action="unassigned").exists())
        services.claim_ticket(self.t.pk, self.tech2)  # снова доступна другим

    def test_comment_added_by_any_staff(self):
        self.login(self.tech2).post(reverse("ticket_comment", args=[self.t.pk]), {"text": "Проверил кабель"})
        self.assertEqual(self.t.comments.get().author, self.tech2)
        self.login(self.tech2).post(reverse("ticket_comment", args=[self.t.pk]), {"text": "  "})
        self.assertEqual(self.t.comments.count(), 1)

    def test_done_ticket_is_frozen_for_technicians(self):
        services.change_status(self.t.pk, self.tech1, Status.DONE, resolution="ok")
        with self.assertRaises(services.ServiceError):
            services.change_status(self.t.pk, self.tech1, Status.IN_PROGRESS)
        with self.assertRaises(services.ServiceError):
            services.release_ticket(self.t.pk, self.tech1)


class AdminAssignTests(HelpesTestCase):
    # 11
    def test_admin_assigns_and_reassigns(self):
        t = self.make_ticket()
        c = self.login(self.admin)
        c.post(reverse("ticket_assign", args=[t.pk]), {"technician": self.tech1.pk})
        t.refresh_from_db()
        self.assertEqual(t.assignee, self.tech1)
        c.post(reverse("ticket_assign", args=[t.pk]), {"technician": self.tech2.pk})
        t.refresh_from_db()
        self.assertEqual(t.assignee, self.tech2)
        acts = list(t.history.filter(action__in=["assigned", "reassigned"]).values_list("action", "old_value", "new_value"))
        self.assertEqual(acts, [("assigned", "не назначена", "Иван"), ("reassigned", "Иван", "Пётр")])

    def test_admin_can_reassign_in_progress_ticket_regardless_of_owner(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        services.assign_ticket(t.pk, self.admin, self.tech2)
        t.refresh_from_db()
        self.assertEqual((t.assignee, t.status), (self.tech2, Status.IN_PROGRESS))

    def test_cannot_assign_to_inactive_or_non_technician(self):
        t = self.make_ticket()
        gone = make_user("gone", tech=True, active=False)
        for bad in (gone, self.admin):
            with self.assertRaises(services.ServiceError):
                services.assign_ticket(t.pk, self.admin, bad)

    def test_admin_returns_ticket_to_queue(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        services.release_ticket(t.pk, self.admin)
        t.refresh_from_db()
        self.assertEqual((t.status, t.assignee), (Status.NEW, None))

    def test_reopen_keeps_previous_completion_in_history(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        services.change_status(t.pk, self.tech1, Status.DONE, resolution="Заменил блок питания")
        services.reopen_ticket(t.pk, self.admin, "Снова сломалось")
        t.refresh_from_db()
        self.assertEqual((t.status, t.assignee), (Status.IN_PROGRESS, self.tech1))
        self.assertIsNone(t.completed_at)
        self.assertTrue(t.history.filter(action="completed", note="Заменил блок питания").exists())
        reopened = t.history.get(action="reopened")
        self.assertIn("Заменил блок питания", reopened.note)
        self.assertIn("Снова сломалось", reopened.note)
        with self.assertRaises(services.ServiceError):
            services.reopen_ticket(t.pk, self.tech1)  # не админ

    def test_reopen_with_inactive_assignee_goes_to_queue(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        services.change_status(t.pk, self.tech1, Status.DONE, resolution="ok")
        self.tech1.is_active = False
        self.tech1.save()
        services.reopen_ticket(t.pk, self.admin)
        t.refresh_from_db()
        self.assertEqual((t.status, t.assignee), (Status.NEW, None))


class QueueViewTests(HelpesTestCase):
    def test_queue_shows_required_columns_and_filters(self):
        t = self.make_ticket(employee_name="Мария", summary="Не печатает",
                             items=[(self.cat_printer, 1), (self.cat_pc, 4)])
        other = self.make_ticket(summary="Нет интернета")
        services.claim_ticket(other.pk, self.tech2)
        r = self.login(self.tech1).get(reverse("queue"))  # по умолчанию «Свободные»
        for text in (t.number, "Мария", "Кабинет 201", "Принтер — 1", "Компьютер или ноутбук — 4",
                     "Не печатает", "Новая"):
            self.assertContains(r, text)
        self.assertNotContains(r, "Нет интернета")
        r = self.client.get(reverse("queue"), {"scope": "open", "q": "интернет"})
        self.assertContains(r, "Нет интернета")
        self.assertNotContains(r, "Не печатает")
        r = self.client.get(reverse("queue"), {"scope": "mine"})
        self.assertNotContains(r, "Нет интернета")
        r = self.client.get(reverse("queue"), {"scope": "open", "assignee": self.tech2.pk})
        self.assertContains(r, other.number)
        self.assertNotContains(r, t.number)

    def test_stale_tickets_are_tracked(self):
        t = self.make_ticket()
        Ticket.objects.filter(pk=t.pk).update(updated_at=timezone.now() - timedelta(hours=100))
        fresh = self.make_ticket(summary="Свежая")
        r = self.login(self.admin).get(reverse("queue"), {"scope": "stale"})
        self.assertContains(r, t.number)
        self.assertNotContains(r, fresh.number)
        self.assertEqual(self.client.get(reverse("panel")).context["stale"], 1)

    def test_detail_shows_history_and_comments(self):
        t = self.make_ticket()
        services.claim_ticket(t.pk, self.tech1)
        r = self.login(self.tech1).get(reverse("ticket_detail", args=[t.pk]))
        self.assertContains(r, "Взята в работу")
        self.assertContains(r, "Заявка создана")
