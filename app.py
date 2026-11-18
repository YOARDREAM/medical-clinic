import hashlib
import os
import secrets
from datetime import date
from html import escape

from flask import Flask, abort, redirect, request, session
from psycopg import Error, connect
from psycopg.rows import dict_row

app = Flask(__name__, static_folder="web", static_url_path="/static")
app.secret_key = os.environ.get("APP_SECRET") or secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=64 * 1024, SESSION_COOKIE_SAMESITE="Strict")

APPOINTMENT_STATUS = {
    "created": "Создана", "confirmed": "Подтверждена",
    "completed": "Завершена", "cancelled": "Отменена",
}
INVOICE_STATUS = {"issued": "Выставлен", "paid": "Оплачен", "cancelled": "Отменён"}
PAYMENT_METHOD = {"cash": "Наличные", "card": "Карта", "transfer": "Перевод"}
SEX = {"F": "Женский", "M": "Мужской"}

# Поле формы: имя столбца, подпись, тип / список выбора, обязательность.
def field(name, label, kind="text", required=True):
    return name, label, kind, required


PERSON = [
    field("last_name", "Фамилия"), field("first_name", "Имя"),
    field("middle_name", "Отчество", required=False),
]
TABLES = {
    "appointments": ("Записи на приём", [
        field("patient_id", "Пациент", "patients"), field("doctor_id", "Врач", "doctors"),
        field("service_id", "Услуга", "services"), field("visit_date", "Дата", "date"),
        field("visit_time", "Время", "time"), field("reason", "Причина обращения", "textarea"),
        field("status", "Статус", APPOINTMENT_STATUS),
    ], ["visit_date", "visit_time", "patient_id", "doctor_id", "service_id", "status"]),
    "patients": ("Пациенты", PERSON + [
        field("sex", "Пол", SEX), field("birth_date", "Дата рождения", "date"),
        field("address", "Адрес"), field("phone", "Телефон"),
        field("oms", "Полис ОМС", required=False), field("snils", "СНИЛС", required=False),
        field("passport_series", "Серия паспорта", required=False),
        field("passport_number", "Номер паспорта", required=False),
        field("login", "Логин"), field("password_hash", "Пароль", "password"),
        field("benefit_category_id", "Категория льготы", "benefit_categories", False),
        field("benefit_document", "Документ на льготу", required=False),
    ], ["last_name", "first_name", "middle_name", "phone", "login", "benefit_category_id"]),
    "doctors": ("Врачи", PERSON + [
        field("birth_date", "Дата рождения", "date"), field("phone", "Телефон"),
        field("diploma_number", "Номер диплома"), field("position_id", "Должность", "positions"),
        field("specialization_id", "Специализация", "specializations"),
    ], ["last_name", "first_name", "middle_name", "position_id", "specialization_id"]),
    "medical_cards": ("Медицинские карты", [
        field("patient_id", "Пациент", "patients"), field("opened_on", "Дата открытия", "date"),
        field("updated_on", "Дата обновления", "date"),
    ], ["patient_id", "opened_on", "updated_on"]),
    "examination_results": ("Результаты осмотров", [
        field("appointment_id", "Запись на приём", "appointments"),
        field("diagnosis_id", "Диагноз", "diagnoses", False),
        field("conclusion", "Заключение", "textarea"),
        field("findings", "Исследования", "textarea", False),
        field("prescriptions", "Назначения", "textarea", False),
        field("recorded_on", "Дата оформления", "date"),
    ], ["appointment_id", "diagnosis_id", "conclusion", "recorded_on"]),
    "invoices": ("Счета", [
        field("appointment_id", "Запись на приём", "appointments"),
        field("issued_on", "Дата выставления", "date"), field("status", "Статус", INVOICE_STATUS),
        field("paid_on", "Дата оплаты", "date", False),
        field("payment_method", "Способ оплаты", PAYMENT_METHOD, False),
    ], ["appointment_id", "issued_on", "status", "total", "paid_on"]),
    "services": ("Медицинские услуги", [
        field("code", "Код услуги"), field("name", "Наименование"), field("price", "Цена, ₽", "money"),
        field("duration_minutes", "Длительность, мин", "integer"),
    ], ["code", "name", "price", "duration_minutes"]),
    "diagnoses": ("Диагнозы", [
        field("icd_code", "Код МКБ"), field("name", "Наименование"),
    ], ["icd_code", "name"]),
    "positions": ("Должности", [
        field("name", "Наименование"), field("category", "Категория"), field("rate", "Ставка", "money"),
    ], ["name", "category", "rate"]),
    "specializations": ("Специализации", [
        field("code", "Код"), field("name", "Наименование"),
    ], ["code", "name"]),
    "benefit_categories": ("Категории льгот", [
        field("name", "Наименование"), field("discount_percent", "Скидка, %", "money"),
        field("confirmation_requirements", "Условия подтверждения", "textarea"),
    ], ["name", "discount_percent", "confirmation_requirements"]),
}
NAV_GROUPS = (
    ("Работа клиники", ("appointments", "patients", "doctors", "medical_cards", "examination_results", "invoices")),
    ("Справочники", ("services", "diagnoses", "positions", "specializations", "benefit_categories")),
)
LABEL_QUERIES = {
    "patients": "SELECT id, concat_ws(' ', last_name, first_name, middle_name) AS label FROM patients",
    "doctors": "SELECT id, concat_ws(' ', last_name, first_name, middle_name) AS label FROM doctors",
    "positions": "SELECT id, concat(name, ' · ', category) AS label FROM positions",
    "services": "SELECT id, concat(name, ' · ', code) AS label FROM services",
    "diagnoses": "SELECT id, concat(icd_code, ' · ', name) AS label FROM diagnoses",
    "specializations": "SELECT id, concat(name, ' · ', code) AS label FROM specializations",
    "benefit_categories": "SELECT id, name AS label FROM benefit_categories",
    "appointments": """SELECT a.id, concat_ws(' · ', concat_ws(' ', p.last_name, p.first_name, p.middle_name),
                      to_char(a.visit_date, 'DD.MM.YYYY'), to_char(a.visit_date + a.visit_time, 'HH24:MI'),
                      s.name) AS label FROM appointments a JOIN patients p ON p.id = a.patient_id
                      JOIN services s ON s.id = a.service_id""",
}


def database():
    return connect(os.environ["DATABASE_URL"], row_factory=dict_row, connect_timeout=5)


def e(value):
    return escape("" if value is None else str(value), quote=True)


def csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    token = session.get("csrf")
    if not token or not secrets.compare_digest(token, request.form.get("csrf", "")):
        abort(403)


def catalog(db, name):
    rows = db.execute(LABEL_QUERIES[name] + " ORDER BY label, id").fetchall()
    return {str(row["id"]): row["label"] for row in rows}


def catalogs(db, fields):
    return {key: catalog(db, kind) for key, _, kind, _ in fields
            if isinstance(kind, str) and kind in LABEL_QUERIES}


def format_value(row, key, options):
    value = row.get(key)
    if value is None:
        return "—"
    if key == "status":
        return f'<span class="status {e(value)}">{e(options[key][value])}</span>'
    if key in options:
        return e(options[key].get(str(value), value))
    if key in ("visit_date", "opened_on", "updated_on", "recorded_on", "issued_on", "paid_on"):
        return e(value.strftime("%d.%m.%Y") if isinstance(value, date) else value)
    if key == "visit_time":
        return e(str(value)[:5])
    if key in ("price", "total", "base_price"):
        return e(f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽")
    return e(value)


def layout(title, current, content):
    menu = [f'<a class="{"active" if not current else ""}" href="/">Обзор</a>']
    for heading, tables in NAV_GROUPS:
        menu.append(f'<p>{e(heading)}</p>')
        for table in tables:
            active = ' class="active" aria-current="page"' if table == current else ""
            menu.append(f'<a href="/t/{table}"{active}>{e(TABLES[table][0])}</a>')
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · Медицинская клиника</title><link rel="stylesheet" href="/static/style.css"></head>
<body><aside><a class="brand" href="/"><span>+</span> Медицинская<br>клиника</a><nav>{''.join(menu)}</nav></aside>
<main>{content}</main></body></html>"""


def heading(title, subtitle="", action=""):
    description = f"<p>{e(subtitle)}</p>" if subtitle else ""
    return f'<header class="page-head"><div><h1>{e(title)}</h1>{description}</div>{action}</header>'


def add_link(table):
    return f'<a class="button" href="/t/{table}/new">+ Добавить</a>'


def get_table(table):
    if table not in TABLES:
        abort(404)
    return TABLES[table]


def render_list(table, rows, lookups, columns):
    _, fields, _ = TABLES[table]
    labels = {key: label for key, label, _, _ in fields} | {"total": "К оплате"}
    options = {key: kind for key, _, kind, _ in fields if isinstance(kind, dict)} | lookups
    headers = "".join(f"<th>{e(labels[key])}</th>" for key in columns)
    body = []
    for row in rows:
        values = "".join(f"<td>{format_value(row, key, options)}</td>" for key in columns)
        base = f'/t/{table}/{row["id"]}'
        card = f'<a href="{base}/history">История</a>' if table == "medical_cards" else ""
        body.append(f'<tr>{values}<td class="actions">{card}<a href="{base}/edit">Изменить</a><a class="danger" href="{base}/delete">Удалить</a></td></tr>')
    if not rows:
        body.append(f'<tr><td colspan="{len(columns) + 1}" class="empty">Пока нет записей</td></tr>')
    return f'<div class="table-wrap"><table><thead><tr>{headers}<th>Действия</th></tr></thead><tbody>{"".join(body)}</tbody></table></div>'


@app.get("/")
def home():
    with database() as db:
        counts = {table: db.execute(f"SELECT count(*) AS total FROM {table}").fetchone()["total"]
                  for table in ("appointments", "patients", "doctors", "invoices")}
        visits = db.execute("""SELECT * FROM appointments
                               ORDER BY visit_date DESC, visit_time DESC, id DESC LIMIT 5""").fetchall()
        lookups = catalogs(db, TABLES["appointments"][1])
    cards = "".join(f'<a class="metric" href="/t/{key}"><span>{e(TABLES[key][0])}</span><strong>{count}</strong></a>'
                    for key, count in counts.items())
    return render_home(cards, visits, lookups)


def render_home(cards, visits, lookups):
    summary = render_list("appointments", visits, lookups,
                          ["visit_date", "visit_time", "patient_id", "doctor_id", "status"])
    return layout("Обзор", "", heading("Обзор клиники", "Записи, медицинские карты и счета",
                                       '<a class="button" href="/t/appointments/new">+ Записать на приём</a>')
                  + f'<section class="metrics">{cards}</section>'
                  + '<div class="section-head"><h2>Последние записи</h2><a href="/t/appointments">Все записи →</a></div>'
                  + summary)


@app.get("/t/<table>")
def list_records(table):
    title, fields, columns = get_table(table)
    source = ("invoices JOIN invoice_totals USING (id)" if table == "invoices" else table)
    order = "visit_date DESC, visit_time DESC, id DESC" if table == "appointments" else "id DESC"
    with database() as db:
        rows = db.execute(f"SELECT * FROM {source} ORDER BY {order}").fetchall()
        lookups = catalogs(db, fields)
    return layout(title, table, heading(title, f"Записей в списке: {len(rows)}", add_link(table))
                  + render_list(table, rows, lookups, columns))


def field_input(spec, old, lookups, editing):
    key, label, kind, required = spec
    value = old.get(key)
    if key == "password_hash":
        value = ""
        if editing:
            label = "Новый пароль (по желанию)"
            required = False
    attrs = f'id="{key}" name="{key}"' + (" required" if required else "")
    if isinstance(kind, dict) or key in lookups:
        options = kind if isinstance(kind, dict) else lookups[key]
        choices = [f'<option value="">{e("Выберите значение" if required else "—")}</option>']
        for code, name in options.items():
            selected = " selected" if str(value) == str(code) else ""
            choices.append(f'<option value="{e(code)}"{selected}>{e(name)}</option>')
        control = f'<select {attrs}>{"".join(choices)}</select>'
    elif kind == "textarea":
        control = f'<textarea {attrs} rows="3">{e(value)}</textarea>'
    else:
        step = ' step="0.01"' if kind == "money" else ""
        input_type = "number" if kind in ("money", "integer") else kind
        autocomplete = ' autocomplete="new-password"' if kind == "password" else ""
        if kind == "time" and value:
            value = str(value)[:5]
        control = f'<input {attrs} type="{input_type}" value="{e(value)}"{step}{autocomplete}>'
    grid = " wide" if kind == "textarea" else ""
    return f'<div class="field{grid}"><label for="{key}">{e(label)}</label>{control}</div>'


def show_form(table, ident, fields, values, lookups, error=None, totals=None):
    editing = ident is not None
    table_title = TABLES[table][0]
    title = "Редактирование записи" if editing else "Новая запись"
    if table == "appointments":
        title = "Редактирование приёма" if editing else "Запись на приём"
    inputs = "".join(field_input(spec, values, lookups, editing) for spec in fields)
    alert = f'<p class="alert" role="alert">{e(error)}</p>' if error else ""
    info = ""
    if totals:
        info = f'<p>Тариф {e(totals["base_price"])} ₽ · Скидка {e(totals["discount_percent"])}% · К оплате {e(totals["total"])} ₽</p>'
    form = f'<form method="post" class="form"><input type="hidden" name="csrf" value="{csrf()}">{info}<div class="form-grid">{inputs}</div><div class="form-actions"><button>Сохранить</button><a href="/t/{table}">К списку</a></div></form>'
    return layout(title, table, f'<a class="back" href="/t/{table}">← {e(table_title)}</a>'
                  + heading(title, f"Запись № {ident}" if editing else table_title) + alert + form)


def database_error(exc):
    state = exc.sqlstate
    primary = exc.diag.message_primary or ""
    if state == "23514" and primary and primary[0].lower() in "абвгдежзийклмнопрстуфхцчшщъыьэюя":
        return primary
    return {
        "23P01": "У врача или пациента уже занят выбранный интервал.",
        "23505": "Запись с таким уникальным значением уже есть.",
        "23503": "Связанная запись отсутствует или на неё ссылаются другие данные.",
        "23502": "Заполните обязательные поля.",
        "23514": "Данные нарушают ограничение базы. Проверьте форматы, даты и заполнение связанных полей.",
        "22007": "Проверьте формат даты или времени.",
        "22008": "Дата или время вне допустимого диапазона.",
        "22P02": "Проверьте формат числа, даты или времени.",
        "22003": "Число вне допустимого диапазона.",
    }.get(state, "База не приняла операцию. Повторите попытку.")


def form_values(fields, editing):
    values = {}
    for key, _, kind, _ in fields:
        raw = request.form.get(key, "")
        value = raw if kind == "password" else raw.strip()
        if kind == "password" and not value and editing:
            continue
        if kind == "password" and value:
            salt = secrets.token_bytes(16)
            digest = hashlib.pbkdf2_hmac("sha256", value.encode(), salt, 600_000)
            value = f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"
        values[key] = value or None
    return values


@app.route("/t/<table>/new", methods=["GET", "POST"], defaults={"ident": None})
@app.route("/t/<table>/<int:ident>/edit", methods=["GET", "POST"])
def edit_record(table, ident):
    _, fields, _ = get_table(table)
    editing = ident is not None
    error = None
    if request.method == "POST":
        check_csrf()
        values = form_values(fields, editing)
        keys = tuple(values)
        args = tuple(values.values())
        try:
            with database() as db:
                if editing:
                    assignments = ", ".join(f"{key} = %s" for key in keys)
                    result = db.execute(f"UPDATE {table} SET {assignments} WHERE id = %s", (*args, ident))
                    if not result.rowcount:
                        abort(404)
                else:
                    placeholders = ", ".join(["%s"] * len(keys))
                    db.execute(f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})", args)
            return redirect(f"/t/{table}", 303)
        except Error as exc:
            error = database_error(exc)
    with database() as db:
        record = db.execute(f"SELECT * FROM {table} WHERE id = %s", (ident,)).fetchone() if editing else {}
        if record is None:
            abort(404)
        if error:
            values = {key: request.form.get(key, "") for key, _, kind, _ in fields if kind != "password"}
        elif editing:
            values = record
        else:
            values = {"status": {"appointments": "created", "invoices": "issued"}.get(table)}
            values.update({key: date.today() for key in ("opened_on", "updated_on", "recorded_on", "issued_on", "visit_date")})
        lookups = catalogs(db, fields)
        totals = (db.execute("SELECT base_price, discount_percent, total FROM invoices JOIN invoice_totals USING (id) WHERE id = %s",
                             (ident,)).fetchone() if editing and table == "invoices" else None)
    return show_form(table, ident, fields, values, lookups, error, totals), 422 if error else 200


@app.route("/t/<table>/<int:ident>/delete", methods=["GET", "POST"])
def delete_record(table, ident):
    title, _, _ = get_table(table)
    alert = ""
    status = 200
    if request.method == "POST":
        check_csrf()
        try:
            with database() as db:
                if not db.execute(f"DELETE FROM {table} WHERE id = %s", (ident,)).rowcount:
                    abort(404)
            return redirect(f"/t/{table}", 303)
        except Error as exc:
            alert = f'<p class="alert" role="alert">{e(database_error(exc))}</p>'
            status = 422
    with database() as db:
        if not db.execute(f"SELECT id FROM {table} WHERE id = %s", (ident,)).fetchone():
            abort(404)
    form = f'<form method="post" class="form"><input type="hidden" name="csrf" value="{csrf()}"><div class="form-actions"><button class="danger-button">Удалить</button><a href="/t/{table}">Отмена</a></div></form>'
    return layout("Удаление записи", table, heading("Удаление записи", f"{title} · № {ident}") + alert + form), status


@app.get("/t/medical_cards/<int:ident>/history")
def history(ident):
    with database() as db:
        card = db.execute("""SELECT p.*, m.opened_on, m.updated_on FROM medical_cards m
                             JOIN patients p ON p.id = m.patient_id WHERE m.id = %s""",
                          (ident,)).fetchone()
        if not card:
            abort(404)
        results = db.execute("""SELECT e.*, a.visit_date, a.visit_time, d.name AS diagnosis,
                                concat_ws(' ', dr.last_name, dr.first_name, dr.middle_name) AS doctor
                                FROM examination_results e JOIN appointments a ON a.id = e.appointment_id
                                JOIN doctors dr ON dr.id = a.doctor_id
                                LEFT JOIN diagnoses d ON d.id = e.diagnosis_id
                                WHERE a.patient_id = %s ORDER BY a.visit_date DESC, a.visit_time DESC""",
                             (card["id"],)).fetchall()
    name = " ".join(card[key] for key in ("last_name", "first_name", "middle_name") if card[key])
    items = []
    for row in results:
        entries = "".join(f'<dt>{label}</dt><dd>{e(row[key] or "—")}</dd>'
                          for key, label in (("diagnosis", "Диагноз"), ("conclusion", "Заключение"),
                                             ("findings", "Исследования"), ("prescriptions", "Назначения")))
        items.append(f'<article class="form"><h2>{format_value(row, "visit_date", {})} · {e(row["doctor"])}</h2><dl>{entries}</dl></article>')
    body = "".join(items) or "<p>В карте пока нет результатов осмотров.</p>"
    return layout("История осмотров", "medical_cards", heading("История осмотров", f"{name} · Карта № {ident}") + body)


@app.errorhandler(404)
def missing(_):
    return layout("Страница не найдена", "", heading("Страница не найдена") + '<a href="/">К обзору</a>'), 404


@app.errorhandler(Error)
def unavailable(_):
    return layout("Ошибка базы данных", "", heading("База данных недоступна", "Проверьте подключение PostgreSQL и применение схемы.")), 503


if __name__ == "__main__":
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("Установите переменную DATABASE_URL. Команды приведены в README.md.")
    app.run(host=os.environ.get("APP_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
