DEFAULT_CATEGORIES = [
    "Компьютер или ноутбук",
    "Принтер",
    "Сканер",
    "Монитор и периферия",
    "Интернет, Wi-Fi или локальная сеть",
    "Программное обеспечение",
    "Другое",
]
GROUPS = ["Администраторы", "Техники"]


def ensure_defaults(group_model=None, category_model=None):
    from django.contrib.auth.models import Group

    from .models import EquipmentCategory

    group_model = group_model or Group
    category_model = category_model or EquipmentCategory
    for name in GROUPS:
        group_model.objects.get_or_create(name=name)
    for i, name in enumerate(DEFAULT_CATEGORIES, start=1):
        category_model.objects.get_or_create(name=name, defaults={"sort_order": i * 10})
