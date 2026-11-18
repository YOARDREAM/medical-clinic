"""Integration checks for a fresh Compose demo instance."""
import os
import re
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

BASE = os.environ.get("CLINIC_URL", "http://127.0.0.1:8000").rstrip("/")
client = build_opener(HTTPCookieProcessor(CookieJar()))


def get(path):
    with client.open(BASE + path, timeout=10) as response:
        return response.read().decode()


def submit(path, expected_status=200, **fields):
    form = get(path)
    token = re.search(r'name="csrf" value="([^"]+)"', form).group(1)
    request = Request(BASE + path, data=urlencode(fields | {"csrf": token}).encode())
    try:
        with client.open(request, timeout=10) as response:
            status, body = response.status, response.read().decode()
    except HTTPError as response:
        status, body = response.code, response.read().decode()
    assert status == expected_status, (path, status, expected_status)
    return body


tables = (
    "positions", "specializations", "benefit_categories", "doctors", "patients",
    "services", "diagnoses", "appointments", "medical_cards", "examination_results", "invoices",
)
home = get("/")
assert "Обзор клиники" in home
for table in tables:
    assert "+ Добавить" in get(f"/t/{table}")
    assert "Сохранить" in get(f"/t/{table}/new")
print(f"Lists and creation forms responded for all {len(tables)} tables.")

form = get("/t/appointments/3/edit")
assert "Ким Мария Викторовна" in form
visit_date = re.search(r'name="visit_date" required type="date" value="([^"]+)"', form).group(1)
assert "Учебная запись об осмотре" in get("/t/medical_cards/1/history")
assert "1 350,00 ₽" in get("/t/invoices")

marker = uuid4().hex[:10]
diagnosis = {
    "icd_code": f"QA-{marker}",
    "name": f"Учебная проверка {marker}",
}
listing = submit("/t/diagnoses/new", **diagnosis)
row = next(row for row in listing.split("</tr>") if diagnosis["name"] in row)
item_id = re.search(r"/t/diagnoses/(\d+)/edit", row).group(1)
try:
    updated = diagnosis | {"name": f"Изменённая проверка {marker}"}
    listing = submit(f"/t/diagnoses/{item_id}/edit", **updated)
    assert updated["name"] in listing
    body = submit("/t/diagnoses/new", expected_status=422, **updated)
    assert "уникальным значением" in body
finally:
    listing = submit(f"/t/diagnoses/{item_id}/delete")
    assert diagnosis["icd_code"] not in listing

selected = dict(
    patient_id=2, doctor_id=1, service_id=1, visit_date=visit_date,
    visit_time="10:05", reason=f"Пересечение {marker}", status="created",
)
# The seeded third booking starts at 10:00 the following day.
conflict = submit("/t/appointments/new", expected_status=422, **selected)
assert "уже занят выбранный интервал" in conflict
assert selected["reason"] not in get("/t/appointments")
blocked = submit("/t/doctors/1/delete", expected_status=422)
assert "ссылаются другие данные" in blocked
print("Create, update, delete, uniqueness, foreign-key restriction and overlap checks passed.")

# Exercise the complete linked lifecycle using only the HTTP forms.
def record_ids(table, listing):
    return set(re.findall(rf'/t/{table}/(\d+)/edit', listing))


created = []
def create_and_edit(table, fields, changes):
    path = f"/t/{table}"
    before = record_ids(table, get(path))
    result = submit(path + "/new", **fields)
    added = record_ids(table, result) - before
    assert len(added) == 1, (table, added)
    ident = added.pop()
    created.append((table, ident))
    result = submit(f"{path}/{ident}/edit", **(fields | changes))
    assert ident in record_ids(table, result)
    return ident


try:
    position = create_and_edit("positions", dict(name=marker, category="Первая", rate="1"), {"category": "Высшая"})
    specialty = create_and_edit("specializations", dict(code=marker, name="Терапия"), {"name": "Кардиология"})
    benefit = create_and_edit("benefit_categories", dict(name=marker, discount_percent=5, confirmation_requirements="Справка"),
                              {"discount_percent": 10})
    service = create_and_edit("services", dict(code=marker, name="Консультация", price=2100, duration_minutes=30), {"price": 1500})
    person = dict(last_name="Учебный", first_name="Тест", birth_date="1990-01-01", phone="+70000000009")
    doctor = create_and_edit("doctors", person | dict(diploma_number=marker, position_id=position, specialization_id=specialty),
                             {"first_name": "Врач"})
    patient = create_and_edit("patients", person | dict(sex="M", address="Учебный адрес", login=marker, password_hash=marker,
                              benefit_category_id=benefit, benefit_document=marker), {"first_name": "Пациент", "password_hash": ""})
    card = create_and_edit("medical_cards", dict(patient_id=patient, opened_on="2020-01-01", updated_on="2020-01-01"),
                           {"updated_on": "2020-01-02"})
    visit = dict(patient_id=patient, doctor_id=doctor, service_id=service, visit_date="2021-01-01", visit_time="10:00",
                 reason="Учебный приём", status="created")
    appointment = create_and_edit("appointments", visit, {"status": "confirmed"})
    submit(f"/t/appointments/{appointment}/edit", **(visit | {"status": "completed"}))
    diagnosis = create_and_edit("diagnoses", dict(icd_code=marker, name="Тест"), {"name": "Учебный диагноз"})
    create_and_edit("examination_results", dict(appointment_id=appointment, diagnosis_id=diagnosis,
                    conclusion="Осмотр", recorded_on="2021-01-01"), {"conclusion": marker})
    assert marker in get(f"/t/medical_cards/{card}/history")
    invoice = create_and_edit("invoices", dict(appointment_id=appointment, issued_on="2021-01-01", status="issued"),
                              dict(status="paid", paid_on="2021-01-02", payment_method="card"))
    details = get(f"/t/invoices/{invoice}/edit")
    assert "1350.00" in details and "10.00%" in details
    cancelled = create_and_edit("appointments", visit | {"visit_time": "11:00"}, {"status": "cancelled"})
    assert "Отменена" in get("/t/appointments")
finally:
    for table, ident in reversed(created):
        result = submit(f"/t/{table}/{ident}/delete")
        assert ident not in record_ids(table, result)
print("All four CRUD operations verified across all eleven tables.")
