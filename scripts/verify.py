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
