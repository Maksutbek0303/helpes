"""Роли: группа «Администраторы» (или суперпользователь) и группа «Техники»."""
from functools import wraps

from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

ADMIN_GROUP = "Администраторы"
TECH_GROUP = "Техники"


def is_admin(user):
    return bool(
        user.is_authenticated
        and user.is_active
        and (user.is_superuser or user.groups.filter(name=ADMIN_GROUP).exists())
    )


def is_technician(user):
    return bool(user.is_authenticated and user.is_active and user.groups.filter(name=TECH_GROUP).exists())


def is_staff_member(user):
    return is_admin(user) or is_technician(user)


def active_technicians():
    return get_user_model().objects.filter(is_active=True, groups__name=TECH_GROUP).order_by("first_name", "username")


def _guard(check):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not check(request.user):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


staff_required = _guard(is_staff_member)
admin_required = _guard(is_admin)
technician_required = _guard(is_technician)


def display_name(user):
    return (user.get_full_name() or user.username) if user else ""
