import logging

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db import DatabaseError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from . import services
from .forms import MAX_ITEMS, ItemFormSet, TicketForm
from .models import Ticket

logger = logging.getLogger(__name__)


def _client_ip(request):
    """За доверенным прокси берём ПОСЛЕДНЕЕ значение X-Forwarded-For: его дописал сам прокси,
    а более ранние значения клиент мог подделать."""
    if settings.HELPES_BEHIND_PROXY:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def _rate_limited(request):
    limit, window = settings.HELPES_RATE_LIMIT_COUNT, settings.HELPES_RATE_LIMIT_WINDOW
    if limit <= 0:
        return False
    return cache.get(f"helpes:rl:{_client_ip(request)}", 0) >= limit


def _rate_hit(request):
    key, window = f"helpes:rl:{_client_ip(request)}", settings.HELPES_RATE_LIMIT_WINDOW
    cache.add(key, 0, window)
    try:
        cache.incr(key)
    except ValueError:  # ключ истёк между add и incr
        cache.set(key, 1, window)


@require_http_methods(["GET", "POST"])
def new_ticket(request):
    """Открытая форма сотрудника: без входа и регистрации."""
    if request.method == "POST":
        form = TicketForm(request.POST)
        formset = ItemFormSet(request.POST, prefix="items")
        form_ok, formset_ok = form.is_valid(), formset.is_valid()  # проверяем обе части, чтобы показать все ошибки
        if _rate_limited(request):
            messages.error(request, "Слишком много заявок с вашего устройства за короткое время. "
                                    "Попробуйте позже или позвоните в техническую службу.")
        elif form_ok and formset_ok:
            d = form.cleaned_data
            try:
                ticket = services.create_ticket(
                    employee_name=d["employee_name"],
                    location=d["location"],
                    location_text=d["location_text"],
                    summary=d["summary"],
                    details=d["details"],
                    items=formset.items(),
                )
            except (DatabaseError, services.ServiceError):
                logger.exception("Не удалось сохранить заявку")
                messages.error(request, "Не удалось сохранить заявку. Заявка НЕ создана — "
                                        "ваши данные сохранены в форме, попробуйте отправить ещё раз.")
            else:
                _rate_hit(request)
                return redirect("ticket_created", number=ticket.number)
        else:
            messages.error(request, "Проверьте форму: некоторые поля заполнены неверно.")
    else:
        form = TicketForm()
        formset = ItemFormSet(prefix="items")
    return render(request, "tickets/new_ticket.html", {"form": form, "formset": formset, "max_items": MAX_ITEMS})


def ticket_created(request, number):
    ticket = get_object_or_404(Ticket, number=number)
    return render(request, "tickets/ticket_created.html", {"number": ticket.number})
