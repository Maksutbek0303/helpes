from django.db import migrations


def seed(apps, schema_editor):
    from tickets.seed import ensure_defaults

    ensure_defaults(group_model=apps.get_model("auth", "Group"),
                    category_model=apps.get_model("tickets", "EquipmentCategory"))


class Migration(migrations.Migration):
    dependencies = [("tickets", "0001_initial"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
