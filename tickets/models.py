from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Building(models.Model):
    """Необязательное здание. Если оно одно (или не используется) — форма его не показывает."""

    name = models.CharField("Название", max_length=100, unique=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Доступно в форме", default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "здание"
        verbose_name_plural = "здания"

    def __str__(self):
        return self.name


class Floor(models.Model):
    building = models.ForeignKey(
        Building, verbose_name="Здание", null=True, blank=True, on_delete=models.PROTECT, related_name="floors"
    )
    name = models.CharField("Название этажа", max_length=100, help_text="Например: 1 этаж, Цокольный, 5 этаж")
    sort_order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Доступно в форме", default=True)

    class Meta:
        ordering = ["building__sort_order", "building__name", "sort_order", "name"]
        verbose_name = "этаж"
        verbose_name_plural = "этажи"
        constraints = [
            models.UniqueConstraint(fields=["building", "name"], name="uniq_floor_name_per_building"),
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(building__isnull=True), name="uniq_floor_name_no_building"
            ),
        ]

    def __str__(self):
        return f"{self.building.name}, {self.name}" if self.building_id else self.name


class Location(models.Model):
    """Кабинет или иное помещение."""

    floor = models.ForeignKey(Floor, verbose_name="Этаж", on_delete=models.PROTECT, related_name="locations")
    name = models.CharField("Кабинет / помещение", max_length=100)
    is_active = models.BooleanField("Доступно в форме", default=True)

    class Meta:
        ordering = ["floor__building__sort_order", "floor__sort_order", "floor__name", "name"]
        verbose_name = "кабинет"
        verbose_name_plural = "кабинеты"
        constraints = [models.UniqueConstraint(fields=["floor", "name"], name="uniq_location_name_per_floor")]

    def __str__(self):
        return f"{self.floor}, {self.name}"


class EquipmentCategory(models.Model):
    name = models.CharField("Название", max_length=150, unique=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Доступно в форме", default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "категория"
        verbose_name_plural = "категории оборудования и неисправностей"

    def __str__(self):
        return self.name


class Status(models.TextChoices):
    NEW = "new", "Новая"
    IN_PROGRESS = "in_progress", "В работе"
    WAITING = "waiting", "Ожидает"
    DONE = "done", "Выполнена"


OPEN_STATUSES = (Status.NEW, Status.IN_PROGRESS, Status.WAITING)


class TicketQuerySet(models.QuerySet):
    def open(self):
        return self.exclude(status=Status.DONE)

    def free(self):
        """Свободные: открытые и без исполнителя."""
        return self.open().filter(assignee__isnull=True)

    def stale(self):
        limit = timezone.now() - timedelta(hours=settings.HELPES_STALE_HOURS)
        return self.open().filter(updated_at__lt=limit)


class Ticket(models.Model):
    number = models.CharField("Номер", max_length=20, unique=True, null=True, blank=True, editable=False)
    employee_name = models.CharField("Имя сотрудника", max_length=100)
    location = models.ForeignKey(
        Location, verbose_name="Кабинет", null=True, blank=True, on_delete=models.PROTECT, related_name="tickets"
    )
    location_text = models.CharField("Местоположение (текстом)", max_length=200, blank=True)
    # Снимок названия на момент создания: переименование кабинета не искажает старые заявки.
    place = models.CharField("Местоположение", max_length=300)
    summary = models.CharField("Что не работает", max_length=200)
    details = models.TextField("Подробности", blank=True, max_length=5000)
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Исполнитель",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_tickets",
    )
    resolution = models.TextField("Выполненные действия и результат", blank=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("Обновлена", default=timezone.now, db_index=True)
    completed_at = models.DateTimeField("Выполнена", null=True, blank=True)

    objects = TicketQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "заявка"
        verbose_name_plural = "заявки"

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def is_open(self):
        return self.status != Status.DONE

    @property
    def is_stale(self):
        return self.is_open and self.updated_at < timezone.now() - timedelta(hours=settings.HELPES_STALE_HOURS)


class TicketItem(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="items")
    category = models.ForeignKey(EquipmentCategory, verbose_name="Тип", on_delete=models.PROTECT, related_name="items")
    quantity = models.PositiveIntegerField(
        "Количество", validators=[MinValueValidator(1), MaxValueValidator(1000)], default=1
    )

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gte=1), name="ticketitem_quantity_gte_1")]

    def __str__(self):
        return f"{self.category} × {self.quantity}"


class TicketComment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ticket_comments")
    text = models.TextField("Комментарий", max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class HistoryAction(models.TextChoices):
    CREATED = "created", "Заявка создана"
    CLAIMED = "claimed", "Взята в работу"
    ASSIGNED = "assigned", "Назначена"
    REASSIGNED = "reassigned", "Переназначена"
    UNASSIGNED = "unassigned", "Возвращена в очередь"
    STATUS = "status", "Смена статуса"
    COMPLETED = "completed", "Выполнена"
    REOPENED = "reopened", "Открыта повторно"


class TicketHistory(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="history")
    action = models.CharField(max_length=20, choices=HistoryAction.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )  # null — действие сотрудника (создание заявки)
    old_value = models.CharField(max_length=300, blank=True)
    new_value = models.CharField(max_length=300, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "история заявок"
