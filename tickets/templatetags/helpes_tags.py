from django import template

from ..permissions import display_name

register = template.Library()

BADGES = {"new": "text-bg-primary", "in_progress": "text-bg-warning", "waiting": "text-bg-secondary",
          "done": "text-bg-success"}


@register.inclusion_tag("includes/status.html")
def status_badge(ticket):
    return {"css": BADGES.get(ticket.status, "text-bg-light"), "label": ticket.get_status_display(),
            "stale": ticket.is_stale}


@register.filter
def dname(user):
    return display_name(user)
