from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.forms import formset_factory

from .models import Building, EquipmentCategory, Floor, Location, Status
from .permissions import ADMIN_GROUP, TECH_GROUP, active_technicians, display_name

User = get_user_model()
MAX_ITEMS = 20


class BootstrapMixin:
    """Добавляет Bootstrap-классы ко всем виджетам."""

    def _style(self):
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, (forms.CheckboxInput,)):
                w.attrs["class"] = "form-check-input"
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs["class"] = "form-select"
            elif not isinstance(w, forms.HiddenInput):
                w.attrs["class"] = "form-control"


class StyledForm(BootstrapMixin, forms.Form):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._style()


class StyledModelForm(BootstrapMixin, forms.ModelForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._style()


# ---------- Форма сотрудника ----------

NO_LOCATION = "other"


def location_choices():
    """Активные кабинеты, сгруппированные «Здание · Этаж». Скрытые этажи/здания исключаются."""
    qs = (
        Location.objects.filter(is_active=True, floor__is_active=True)
        .exclude(floor__building__is_active=False)
        .select_related("floor", "floor__building")
    )
    groups = {}
    for loc in qs:
        groups.setdefault(str(loc.floor), []).append((str(loc.pk), loc.name))
    choices = [("", "— выберите кабинет —")]
    choices += [(label, rows) for label, rows in groups.items()]
    choices.append((NO_LOCATION, "Моего кабинета нет в списке"))
    return choices


class TicketForm(StyledForm):
    employee_name = forms.CharField(label="Ваше имя", max_length=100, strip=True)
    location = forms.ChoiceField(label="Кабинет", required=False)
    location_text = forms.CharField(
        label="Где находитесь (если кабинета нет в списке)", max_length=200, required=False, strip=True,
        help_text="Например: 3 этаж, переговорная возле лифта",
    )
    summary = forms.CharField(label="Что не работает", max_length=200, strip=True)
    details = forms.CharField(
        label="Подробности (необязательно)", max_length=5000, required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Как проявляется проблема, когда началась",
    )
    # Ловушка для ботов: настоящий человек это поле не видит и не заполняет.
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["location"].choices = location_choices()
        self.fields["website"].widget.attrs["class"] = ""

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise ValidationError("Не удалось отправить форму.")
        return ""

    def clean(self):
        data = super().clean()
        choice, text = data.get("location"), data.get("location_text")
        if not choice or choice == NO_LOCATION:
            data["location"] = None
            if not text:
                self.add_error("location_text" if choice == NO_LOCATION else "location",
                               "Выберите кабинет из списка или опишите местоположение текстом.")
        else:
            valid = {value for _, rows in self.fields["location"].choices if isinstance(rows, list) for value, _ in rows}
            if choice not in valid:
                self.add_error("location", "Выбранный кабинет недоступен. Выберите другой.")
            else:
                data["location"] = Location.objects.get(pk=int(choice))
        return data


class ItemForm(StyledForm):
    category = forms.ModelChoiceField(
        label="Что сломалось", queryset=EquipmentCategory.objects.none(), empty_label="— выберите —"
    )
    quantity = forms.IntegerField(label="Сколько", min_value=1, max_value=1000, initial=1,
                                  error_messages={"min_value": "Количество должно быть не меньше 1.",
                                                  "invalid": "Введите целое число."})

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["category"].queryset = EquipmentCategory.objects.filter(is_active=True)
        self.fields["quantity"].widget.attrs.update({"inputmode": "numeric", "min": 1})


class BaseItemFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            cat = form.cleaned_data.get("category") if hasattr(form, "cleaned_data") else None
            if cat is None:
                continue
            if cat.pk in seen:
                raise ValidationError(f"Позиция «{cat.name}» указана дважды — объедините количество в одну строку.")
            seen.add(cat.pk)

    def items(self):
        return [
            (f.cleaned_data["category"], f.cleaned_data["quantity"])
            for f in self.forms
            if f.cleaned_data.get("category")
        ]


ItemFormSet = formset_factory(
    ItemForm, formset=BaseItemFormSet, extra=0, min_num=1, validate_min=True, max_num=MAX_ITEMS, validate_max=True
)


# ---------- Работа техника ----------

class StatusForm(StyledForm):
    status = forms.ChoiceField(
        label="Новый статус",
        choices=[(Status.IN_PROGRESS, Status.IN_PROGRESS.label), (Status.WAITING, Status.WAITING.label),
                 (Status.DONE, Status.DONE.label)],
    )
    comment = forms.CharField(label="Комментарий", required=False, max_length=5000,
                              widget=forms.Textarea(attrs={"rows": 2}))
    resolution = forms.CharField(label="Выполненные действия и результат", required=False, max_length=5000,
                                 widget=forms.Textarea(attrs={"rows": 3}),
                                 help_text="Обязательно для статуса «Выполнена»")


class CommentForm(StyledForm):
    text = forms.CharField(label="Комментарий", max_length=5000, widget=forms.Textarea(attrs={"rows": 2}))


class AssignForm(StyledForm):
    technician = forms.ModelChoiceField(label="Исполнитель", queryset=User.objects.none(), empty_label="— выберите —")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["technician"].queryset = active_technicians()
        self.fields["technician"].label_from_instance = display_name


# ---------- Справочники ----------

class BuildingForm(StyledModelForm):
    class Meta:
        model = Building
        fields = ["name", "sort_order", "is_active"]


class FloorForm(StyledModelForm):
    class Meta:
        model = Floor
        fields = ["building", "name", "sort_order", "is_active"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["building"].required = False
        self.fields["building"].empty_label = "— без здания —"

    def clean(self):
        data = super().clean()
        name, building = data.get("name"), data.get("building")
        if name:
            dup = Floor.objects.filter(name__iexact=name, building=building).exclude(pk=self.instance.pk)
            if dup.exists():
                self.add_error("name", "Такой этаж уже есть.")
        return data


class LocationForm(StyledModelForm):
    class Meta:
        model = Location
        fields = ["floor", "name", "is_active"]


class CategoryForm(StyledModelForm):
    class Meta:
        model = EquipmentCategory
        fields = ["name", "sort_order", "is_active"]


# ---------- Пользователи ----------

class UserBaseForm(StyledForm):
    first_name = forms.CharField(label="Имя и фамилия", max_length=150)
    is_technician = forms.BooleanField(label="Техник (берёт заявки в работу)", required=False)
    is_admin = forms.BooleanField(label="Администратор (управляет системой)", required=False)

    def clean(self):
        data = super().clean()
        if not data.get("is_technician") and not data.get("is_admin"):
            raise ValidationError("Выберите хотя бы одну роль.")
        return data


class UserCreateForm(UserBaseForm):
    username = forms.CharField(label="Логин", max_length=150)
    password1 = forms.CharField(label="Пароль", widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(label="Повторите пароль", widget=forms.PasswordInput, strip=False)
    field_order = ["first_name", "username", "password1", "password2", "is_technician", "is_admin"]

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("Такой логин уже занят.")
        return username

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Пароли не совпадают.")
        elif p1:
            try:
                validate_password(p1, User(username=data.get("username", ""), first_name=data.get("first_name", "")))
            except ValidationError as e:
                self.add_error("password1", e)
        return data


class UserEditForm(UserBaseForm):
    password1 = forms.CharField(label="Новый пароль (если нужно сменить)", widget=forms.PasswordInput,
                                strip=False, required=False)
    password2 = forms.CharField(label="Повторите новый пароль", widget=forms.PasswordInput, strip=False, required=False)

    def __init__(self, *a, user=None, **kw):
        super().__init__(*a, **kw)
        self.user = user

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 or p2:
            if p1 != p2:
                self.add_error("password2", "Пароли не совпадают.")
            else:
                try:
                    validate_password(p1, self.user)
                except ValidationError as e:
                    self.add_error("password1", e)
        return data


def apply_roles(user, is_tech, is_admin):
    tech, _ = Group.objects.get_or_create(name=TECH_GROUP)
    adm, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
    user.groups.add(tech) if is_tech else user.groups.remove(tech)
    user.groups.add(adm) if is_admin else user.groups.remove(adm)


class DeactivateForm(StyledForm):
    action = forms.ChoiceField(
        label="Что сделать с его незавершёнными заявками",
        choices=[("queue", "Вернуть в общую очередь"), ("reassign", "Передать другому технику")],
        widget=forms.RadioSelect, initial="queue",
    )
    technician = forms.ModelChoiceField(label="Кому передать", queryset=User.objects.none(), required=False,
                                        empty_label="— выберите —")

    def __init__(self, *a, exclude=None, **kw):
        super().__init__(*a, **kw)
        self.fields["technician"].queryset = active_technicians().exclude(pk=exclude)
        self.fields["technician"].label_from_instance = display_name
        self.fields["action"].widget.attrs["class"] = "form-check-input"

    def clean(self):
        data = super().clean()
        if data.get("action") == "reassign" and not data.get("technician"):
            self.add_error("technician", "Выберите техника, которому передать заявки.")
        return data
