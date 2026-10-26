from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .forms import (
    AssignForm,
    BuildingForm,
    CategoryForm,
    DeactivateForm,
    FloorForm,
    LocationForm,
    UserCreateForm,
    UserEditForm,
    apply_roles,
)
from .models import Building, EquipmentCategory, Floor, Location, Ticket
from .permissions import ADMIN_GROUP, TECH_GROUP, active_technicians, admin_required, is_admin, is_technician

User = get_user_model()


@admin_required
def panel(request):
    technicians = active_technicians().annotate(
        open_count=Count("assigned_tickets", filter=~Q(assigned_tickets__status="done"))
    )
    return render(request, "tickets/panel.html", {
        "free": Ticket.objects.free().count(),
        "stale": Ticket.objects.stale().count(),
        "open": Ticket.objects.open().count(),
        "technicians": technicians,
    })


# ---------- Назначение ----------

@admin_required
@require_POST
def ticket_assign(request, pk):
    form = AssignForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Выберите активного техника.")
    else:
        try:
            services.assign_ticket(pk, request.user, form.cleaned_data["technician"])
            messages.success(request, "Исполнитель назначен.")
        except services.ServiceError as e:
            messages.error(request, str(e))
    return redirect("ticket_detail", pk=pk)


@admin_required
@require_POST
def ticket_reopen(request, pk):
    try:
        services.reopen_ticket(pk, request.user, request.POST.get("note", ""))
        messages.success(request, "Заявка открыта повторно. Предыдущее завершение сохранено в истории.")
    except services.ServiceError as e:
        messages.error(request, str(e))
    return redirect("ticket_detail", pk=pk)


# ---------- Пользователи ----------

def _other_active_admins(exclude_pk):
    return [u for u in User.objects.filter(is_active=True).exclude(pk=exclude_pk) if is_admin(u)]


@admin_required
def user_list(request):
    users = User.objects.prefetch_related("groups").annotate(
        open_count=Count("assigned_tickets", filter=~Q(assigned_tickets__status="done"))
    ).order_by("-is_active", "first_name", "username")
    rows = [{"u": u, "tech": u.groups.filter(name=TECH_GROUP).exists(), "admin": is_admin(u) or
             u.groups.filter(name=ADMIN_GROUP).exists() or u.is_superuser} for u in users]
    return render(request, "tickets/user_list.html", {"rows": rows})


@admin_required
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        user = User.objects.create_user(username=d["username"], password=d["password1"], first_name=d["first_name"])
        apply_roles(user, d["is_technician"], d["is_admin"])
        messages.success(request, f"Учётная запись «{user.username}» создана.")
        return redirect("user_list")
    return render(request, "tickets/form_page.html", {"form": form, "title": "Новый пользователь",
                                                      "back": "user_list", "submit": "Создать"})


@admin_required
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    initial = {"first_name": target.first_name,
               "is_technician": target.groups.filter(name=TECH_GROUP).exists(),
               "is_admin": target.is_superuser or target.groups.filter(name=ADMIN_GROUP).exists()}
    form = UserEditForm(request.POST or None, initial=initial, user=target)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        error = None
        if not d["is_admin"] and is_admin(target):
            if target.pk == request.user.pk:
                error = "Нельзя снять административную роль с самого себя."
            elif target.is_active and not _other_active_admins(target.pk):
                error = "Нельзя убрать роль у последнего активного администратора."
            elif target.is_superuser:
                error = "Это суперпользователь Django: его роль администратора управляется через manage.py."
        if not d["is_technician"] and initial["is_technician"] and services.open_tickets_of(target).exists():
            error = ("У пользователя есть незавершённые заявки. Сначала передайте их через "
                     "«Отключить» или переназначьте на странице заявки.")
        if error:
            form.add_error(None, error)
        else:
            target.first_name = d["first_name"]
            if d["password1"]:
                target.set_password(d["password1"])
            target.save()
            apply_roles(target, d["is_technician"], d["is_admin"] or target.is_superuser)
            messages.success(request, "Пользователь обновлён.")
            return redirect("user_list")
    return render(request, "tickets/form_page.html", {"form": form, "title": f"Пользователь: {target.username}",
                                                      "back": "user_list", "submit": "Сохранить"})


@admin_required
def user_deactivate(request, pk):
    target = get_object_or_404(User, pk=pk)
    open_tickets = list(services.open_tickets_of(target))
    blocker = None
    if target.pk == request.user.pk:
        blocker = "Нельзя отключить собственную учётную запись."
    elif not target.is_active:
        blocker = "Учётная запись уже отключена."
    elif is_admin(target) and not _other_active_admins(target.pk):
        blocker = "Нельзя отключить последнего активного администратора."

    form = DeactivateForm(request.POST or None, exclude=target.pk) if open_tickets else None
    if request.method == "POST" and not blocker and (form is None or form.is_valid()):
        moved = 0
        if form is not None:
            d = form.cleaned_data
            moved = services.transfer_open_tickets(
                target, request.user, d["technician"] if d["action"] == "reassign" else None)
        target.is_active = False
        target.save(update_fields=["is_active"])
        messages.success(request, f"Учётная запись отключена. История работ сохранена."
                                  + (f" Обработано заявок: {moved}." if moved else ""))
        return redirect("user_list")
    return render(request, "tickets/user_deactivate.html", {"target": target, "open_tickets": open_tickets,
                                                            "form": form, "blocker": blocker})


@admin_required
@require_POST
def user_activate(request, pk):
    target = get_object_or_404(User, pk=pk)
    target.is_active = True
    target.save(update_fields=["is_active"])
    messages.success(request, "Учётная запись включена.")
    return redirect("user_list")


# ---------- Справочники ----------

CATALOGS = {
    "buildings": {"model": Building, "form": BuildingForm, "title": "Здания", "single": "здание",
                  "qs": lambda: Building.objects.all()},
    "floors": {"model": Floor, "form": FloorForm, "title": "Этажи", "single": "этаж",
               "qs": lambda: Floor.objects.select_related("building")},
    "locations": {"model": Location, "form": LocationForm, "title": "Кабинеты", "single": "кабинет",
                  "qs": lambda: Location.objects.select_related("floor", "floor__building")},
    "categories": {"model": EquipmentCategory, "form": CategoryForm, "title": "Категории оборудования",
                   "single": "категорию", "qs": lambda: EquipmentCategory.objects.all()},
}


def _catalog(key):
    from django.http import Http404

    if key not in CATALOGS:
        raise Http404
    return CATALOGS[key]


@admin_required
def catalog_list(request, key):
    cfg = _catalog(key)
    return render(request, "tickets/catalog_list.html", {"key": key, "cfg": cfg, "objects": cfg["qs"]()})


@admin_required
def catalog_edit(request, key, pk=None):
    cfg = _catalog(key)
    instance = get_object_or_404(cfg["model"], pk=pk) if pk else None
    form = cfg["form"](request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Сохранено.")
        return redirect("catalog_list", key=key)
    title = f"Изменить: {cfg['single']}" if instance else f"Добавить: {cfg['single']}"
    return render(request, "tickets/form_page.html", {"form": form, "title": title, "back": "catalog_list",
                                                      "back_args": [key], "submit": "Сохранить"})


@admin_required
@require_POST
def catalog_toggle(request, key, pk):
    """Деактивация вместо удаления: исторические заявки продолжают ссылаться на запись."""
    cfg = _catalog(key)
    obj = get_object_or_404(cfg["model"], pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active"])
    messages.success(request, "Теперь доступно в форме." if obj.is_active else "Скрыто из формы (история сохранена).")
    return redirect("catalog_list", key=key)
