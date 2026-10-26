from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .forms import AssignForm, CommentForm, StatusForm
from .models import Status, Ticket
from .permissions import (
    active_technicians,
    display_name,
    is_admin,
    is_technician,
    staff_required,
    technician_required,
)

SCOPES = {
    "free": "Свободные",
    "mine": "Мои",
    "open": "Все открытые",
    "stale": "Давно без обновлений",
    "done": "Выполненные",
}


@staff_required
def queue(request):
    user = request.user
    default_scope = "free" if is_technician(user) else "open"
    scope = request.GET.get("scope", default_scope)
    if scope not in SCOPES:
        scope = default_scope

    base = Ticket.objects.select_related("assignee").prefetch_related("items__category")
    counts = {
        "free": Ticket.objects.free().count(),
        "mine": Ticket.objects.open().filter(assignee=user).count(),
        "open": Ticket.objects.open().count(),
        "stale": Ticket.objects.stale().count(),
    }
    qs = {
        "free": base.free(),
        "mine": base.open().filter(assignee=user),
        "open": base.open(),
        "stale": base.stale(),
        "done": base.filter(status=Status.DONE),
    }[scope]

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(employee_name__icontains=q) | Q(place__icontains=q)
                       | Q(summary__icontains=q))
    status = request.GET.get("status", "")
    if status in Status.values:
        qs = qs.filter(status=status)
    assignee = request.GET.get("assignee", "")
    if assignee.isdigit():
        qs = qs.filter(assignee_id=int(assignee))

    if scope in ("stale",):
        qs = qs.order_by("updated_at")
    elif scope == "free":
        qs = qs.order_by("created_at")  # самые старые сверху — их надо брать первыми
    page = Paginator(qs, 25).get_page(request.GET.get("page"))

    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "tickets/queue.html", {
        "page": page, "scope": scope, "scopes": SCOPES, "counts": counts, "q": q, "status": status,
        "assignee": assignee, "statuses": Status.choices, "technicians": active_technicians(),
        "querystring": params.urlencode(), "tickets": page.object_list,
    })


@staff_required
def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket.objects.select_related("assignee"), pk=pk)
    user = request.user
    admin, tech = is_admin(user), is_technician(user)
    mine = ticket.assignee_id == user.pk
    ctx = {
        "ticket": ticket,
        "items": ticket.items.select_related("category"),
        "comments": ticket.comments.select_related("author"),
        "history": ticket.history.select_related("actor"),
        "can_claim": tech and ticket.is_open and ticket.assignee_id is None,
        "can_release": ticket.is_open and ticket.assignee_id is not None and (mine or admin),
        "can_change": ticket.is_open and ticket.assignee_id is not None and (mine or admin),
        "can_assign": admin and ticket.is_open,
        "can_reopen": admin and not ticket.is_open,
        "status_form": StatusForm(initial={"status": Status.IN_PROGRESS}),
        "comment_form": CommentForm(),
        "assign_form": AssignForm() if admin else None,
        "display_name": display_name,
    }
    return render(request, "tickets/ticket_detail.html", ctx)


def _run(request, pk, func, success):
    try:
        func()
    except services.ServiceError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, success)
    return redirect("ticket_detail", pk=pk)


@technician_required
@require_POST
def ticket_claim(request, pk):
    return _run(request, pk, lambda: services.claim_ticket(pk, request.user), "Заявка взята в работу.")


@staff_required
@require_POST
def ticket_release(request, pk):
    return _run(request, pk, lambda: services.release_ticket(pk, request.user), "Заявка возвращена в общую очередь.")


@staff_required
@require_POST
def ticket_status(request, pk):
    form = StatusForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Неверные данные формы статуса.")
        return redirect("ticket_detail", pk=pk)
    d = form.cleaned_data
    return _run(request, pk, lambda: services.change_status(pk, request.user, d["status"], d["comment"],
                                                            d["resolution"]), "Статус заявки обновлён.")


@staff_required
@require_POST
def ticket_comment(request, pk):
    form = CommentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Комментарий не может быть пустым.")
        return redirect("ticket_detail", pk=pk)
    return _run(request, pk, lambda: services.add_comment(pk, request.user, form.cleaned_data["text"]),
                "Комментарий добавлен.")
