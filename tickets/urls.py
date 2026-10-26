from django.contrib.auth import views as auth_views
from django.urls import path

from . import views_panel as panel
from . import views_public as public
from . import views_staff as staff

urlpatterns = [
    path("", public.new_ticket, name="new_ticket"),
    path("created/<str:number>/", public.ticket_created, name="ticket_created"),

    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("staff/", staff.queue, name="queue"),
    path("staff/tickets/<int:pk>/", staff.ticket_detail, name="ticket_detail"),
    path("staff/tickets/<int:pk>/claim/", staff.ticket_claim, name="ticket_claim"),
    path("staff/tickets/<int:pk>/release/", staff.ticket_release, name="ticket_release"),
    path("staff/tickets/<int:pk>/status/", staff.ticket_status, name="ticket_status"),
    path("staff/tickets/<int:pk>/comment/", staff.ticket_comment, name="ticket_comment"),

    path("panel/", panel.panel, name="panel"),
    path("panel/tickets/<int:pk>/assign/", panel.ticket_assign, name="ticket_assign"),
    path("panel/tickets/<int:pk>/reopen/", panel.ticket_reopen, name="ticket_reopen"),
    path("panel/users/", panel.user_list, name="user_list"),
    path("panel/users/new/", panel.user_create, name="user_create"),
    path("panel/users/<int:pk>/edit/", panel.user_edit, name="user_edit"),
    path("panel/users/<int:pk>/deactivate/", panel.user_deactivate, name="user_deactivate"),
    path("panel/users/<int:pk>/activate/", panel.user_activate, name="user_activate"),
    path("panel/catalog/<str:key>/", panel.catalog_list, name="catalog_list"),
    path("panel/catalog/<str:key>/new/", panel.catalog_edit, name="catalog_new"),
    path("panel/catalog/<str:key>/<int:pk>/edit/", panel.catalog_edit, name="catalog_edit"),
    path("panel/catalog/<str:key>/<int:pk>/toggle/", panel.catalog_toggle, name="catalog_toggle"),
]
