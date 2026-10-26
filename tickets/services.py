"""Бизнес-логика заявок. Все изменения исполнителя/статуса проходят здесь и пишутся в историю."""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import (
    HistoryAction,
    Status,
    Ticket,
    TicketComment,
    TicketHistory,
    TicketItem,
)
from .permissions import display_name, is_admin, is_technician

User = get_user_model()


class ServiceError(Exception):
    """Ожидаемая ошибка с понятным пользователю текстом."""


class TicketTaken(ServiceError):
    """Заявку уже забрал другой техник (или она закрыта)."""


def _log(ticket, action, actor, old="", new="", note=""):
    TicketHistory.objects.create(ticket=ticket, action=action, actor=actor, old_value=old, new_value=new, note=note)


def _touch(ticket, **fields):
    for k, v in fields.items():
        setattr(ticket, k, v)
    ticket.updated_at = timezone.now()
    ticket.save()


def _locked(ticket_id):
    try:
        return Ticket.objects.select_related("assignee").select_for_update(of=("self",)).get(pk=ticket_id)
    except Ticket.DoesNotExist:
        raise ServiceError("Заявка не найдена.")


@transaction.atomic
def create_ticket(*, employee_name, location, location_text, summary, details, items):
    """items — список (EquipmentCategory, quantity)."""
    if not items:
        raise ServiceError("Нужна хотя бы одна позиция оборудования.")
    place = str(location) if location else location_text
    ticket = Ticket.objects.create(
        employee_name=employee_name,
        location=location,
        location_text="" if location else location_text,
        place=place,
        summary=summary,
        details=details,
    )
    ticket.number = f"HELP-{ticket.pk:06d}"
    ticket.save(update_fields=["number"])
    TicketItem.objects.bulk_create([TicketItem(ticket=ticket, category=c, quantity=q) for c, q in items])
    _log(ticket, HistoryAction.CREATED, None, new=Status.NEW.label, note=f"Сотрудник: {employee_name}")
    return ticket


def claim_ticket(ticket_id, user):
    """Техник берёт свободную заявку. Условное UPDATE выполняется атомарно на уровне БД,
    поэтому из двух одновременных запросов успешным будет ровно один."""
    if not is_technician(user):
        raise ServiceError("Только технический специалист может брать заявки.")
    with transaction.atomic():
        updated = (
            Ticket.objects.filter(pk=ticket_id, assignee__isnull=True)
            .exclude(status=Status.DONE)
            .update(assignee=user, status=Status.IN_PROGRESS, updated_at=timezone.now())
        )
        if updated != 1:
            if not Ticket.objects.filter(pk=ticket_id).exists():
                raise ServiceError("Заявка не найдена.")
            raise TicketTaken("Заявку уже взял другой специалист или она закрыта.")
        ticket = Ticket.objects.get(pk=ticket_id)
        _log(ticket, HistoryAction.CLAIMED, user, old=Status.NEW.label, new=Status.IN_PROGRESS.label,
             note=f"Исполнитель: {display_name(user)}")
    return ticket


@transaction.atomic
def release_ticket(ticket_id, actor):
    """Вернуть заявку в общую очередь: исполнитель сам или администратор."""
    ticket = _locked(ticket_id)
    if not ticket.is_open:
        raise ServiceError("Выполненную заявку нельзя вернуть в очередь.")
    if ticket.assignee_id is None:
        raise ServiceError("Заявка и так находится в общей очереди.")
    if ticket.assignee_id != actor.pk and not is_admin(actor):
        raise ServiceError("Вернуть в очередь можно только свою заявку.")
    old_assignee, old_status = ticket.assignee, ticket.status
    _touch(ticket, assignee=None, status=Status.NEW)
    _log(ticket, HistoryAction.UNASSIGNED, actor, old=display_name(old_assignee), new="общая очередь",
         note=f"Статус: {Status(old_status).label} → {Status.NEW.label}")
    return ticket


@transaction.atomic
def assign_ticket(ticket_id, actor, technician):
    """Администратор назначает / переназначает заявку."""
    if not is_admin(actor):
        raise ServiceError("Назначать заявки может только администратор.")
    if not (technician.is_active and is_technician(technician)):
        raise ServiceError("Исполнителем может быть только активный техник.")
    ticket = _locked(ticket_id)
    if not ticket.is_open:
        raise ServiceError("Выполненную заявку нельзя назначить. Сначала откройте её повторно.")
    if ticket.assignee_id == technician.pk:
        raise ServiceError("Эта заявка уже назначена выбранному технику.")
    previous = ticket.assignee
    _touch(ticket, assignee=technician)
    _log(
        ticket,
        HistoryAction.REASSIGNED if previous else HistoryAction.ASSIGNED,
        actor,
        old=display_name(previous) if previous else "не назначена",
        new=display_name(technician),
    )
    return ticket


@transaction.atomic
def change_status(ticket_id, actor, new_status, comment="", resolution=""):
    ticket = _locked(ticket_id)
    if new_status not in (Status.IN_PROGRESS, Status.WAITING, Status.DONE):
        raise ServiceError("Недопустимый статус.")
    if not ticket.is_open:
        raise ServiceError("Заявка уже выполнена. Повторно открыть её может администратор.")
    if ticket.assignee_id is None:
        raise ServiceError("У заявки нет исполнителя: сначала возьмите её в работу или назначьте техника.")
    if ticket.assignee_id != actor.pk and not is_admin(actor):
        raise ServiceError("Менять статус может исполнитель заявки или администратор.")
    if ticket.status == new_status:
        raise ServiceError("Заявка уже имеет этот статус.")
    comment, resolution = (comment or "").strip(), (resolution or "").strip()
    if new_status == Status.WAITING and not comment:
        raise ServiceError("Для статуса «Ожидает» напишите комментарий: чего именно ждёте.")
    if new_status == Status.DONE and not resolution:
        raise ServiceError("Для завершения опишите выполненные действия и результат.")

    old = Status(ticket.status)
    fields = {"status": new_status}
    if new_status == Status.DONE:
        fields.update(resolution=resolution, completed_at=timezone.now())
    _touch(ticket, **fields)
    _log(ticket, HistoryAction.STATUS, actor, old=old.label, new=Status(new_status).label)
    if new_status == Status.DONE:
        _log(ticket, HistoryAction.COMPLETED, actor, note=resolution)
    if comment:
        TicketComment.objects.create(ticket=ticket, author=actor, text=comment)
    return ticket


@transaction.atomic
def reopen_ticket(ticket_id, actor, note=""):
    if not is_admin(actor):
        raise ServiceError("Повторно открыть заявку может только администратор.")
    ticket = _locked(ticket_id)
    if ticket.is_open:
        raise ServiceError("Заявка не завершена.")
    prev = f"Предыдущее завершение {timezone.localtime(ticket.completed_at):%d.%m.%Y %H:%M}: {ticket.resolution}"
    keep_assignee = ticket.assignee if (ticket.assignee and ticket.assignee.is_active and is_technician(ticket.assignee)) else None
    new_status = Status.IN_PROGRESS if keep_assignee else Status.NEW
    _touch(ticket, status=new_status, assignee=keep_assignee, resolution="", completed_at=None)
    _log(ticket, HistoryAction.REOPENED, actor, old=Status.DONE.label, new=new_status.label,
         note=(f"{note.strip()}\n" if note.strip() else "") + prev)
    return ticket


def add_comment(ticket_id, author, text):
    text = (text or "").strip()
    if not text:
        raise ServiceError("Комментарий не может быть пустым.")
    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise ServiceError("Заявка не найдена.")
    comment = TicketComment.objects.create(ticket=ticket, author=author, text=text)
    Ticket.objects.filter(pk=ticket_id).update(updated_at=timezone.now())
    return comment


def open_tickets_of(user):
    return Ticket.objects.open().filter(assignee=user).order_by("created_at")


@transaction.atomic
def transfer_open_tickets(from_user, actor, to_user=None):
    """Передать все незавершённые заявки from_user другому технику или вернуть в очередь."""
    if not is_admin(actor):
        raise ServiceError("Только администратор может перераспределять заявки.")
    if to_user is not None and not (to_user.is_active and is_technician(to_user)):
        raise ServiceError("Принять заявки может только активный техник.")
    if to_user is not None and to_user.pk == from_user.pk:
        raise ServiceError("Нельзя передать заявки тому же технику.")
    ids = list(open_tickets_of(from_user).select_for_update().values_list("pk", flat=True))
    for tid in ids:
        ticket = _locked(tid)
        if ticket.assignee_id != from_user.pk or not ticket.is_open:
            continue
        if to_user is None:
            _touch(ticket, assignee=None, status=Status.NEW)
            _log(ticket, HistoryAction.UNASSIGNED, actor, old=display_name(from_user), new="общая очередь",
                 note="Массовая передача при недоступности техника")
        else:
            _touch(ticket, assignee=to_user)
            _log(ticket, HistoryAction.REASSIGNED, actor, old=display_name(from_user), new=display_name(to_user),
                 note="Массовая передача при недоступности техника")
    return len(ids)
