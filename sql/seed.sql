-- Вымышленные учебные данные. Выполнять после schema.sql в новой базе.
BEGIN;
INSERT INTO positions (name, category, rate) VALUES
    ('Врач', 'Высшая', 1), ('Врач', 'Первая', 1);
INSERT INTO specializations (code, name) VALUES
    ('THER', 'Терапия'), ('CARD', 'Кардиология');
INSERT INTO benefit_categories (name, discount_percent, confirmation_requirements)
VALUES ('Учебная льгота', 10, 'Вымышленный документ для демонстрации');
INSERT INTO doctors (last_name, first_name, middle_name, birth_date, phone, diploma_number, position_id, specialization_id)
VALUES ('Орлова', 'Анна', 'Игоревна', '1986-04-05', '+70000000001', 'DEMO-DOC-01', 1, 1),
       ('Волков', 'Михаил', 'Олегович', '1990-10-09', '+70000000002', 'DEMO-DOC-02', 2, 2);
INSERT INTO patients (last_name, first_name, middle_name, sex, birth_date, address, phone, login, password_hash, benefit_category_id, benefit_document)
VALUES ('Смирнов', 'Илья', 'Андреевич', 'M', '2002-05-12', 'Учебный адрес 1', '+70000000011', 'demo-ilya', '!disabled', 1, 'DEMO-BEN-01'),
       ('Ким', 'Мария', 'Викторовна', 'F', '1998-11-17', 'Учебный адрес 2', '+70000000012', 'demo-maria', '!disabled', NULL, NULL);
INSERT INTO services (code, name, price, duration_minutes) VALUES
    ('B01.047.001', 'Первичный приём терапевта', 1500, 30),
    ('B01.015.001', 'Первичный приём кардиолога', 2100, 45);
INSERT INTO diagnoses (icd_code, name) VALUES
    ('Z00.0', 'Общий медицинский осмотр'), ('Z71.1', 'Обращение за консультацией');
INSERT INTO appointments (patient_id, doctor_id, service_id, visit_date, visit_time, reason)
VALUES (1, 1, 1, CURRENT_DATE - 2, '09:00', 'Профилактический осмотр'),
       (1, 2, 2, CURRENT_DATE - 1, '11:00', 'Плановый приём'),
       (2, 1, 1, CURRENT_DATE + 1, '10:00', 'Первичная консультация');
UPDATE appointments SET status = 'confirmed' WHERE id IN (1, 2);
UPDATE appointments SET status = 'completed' WHERE id IN (1, 2);
INSERT INTO medical_cards (patient_id, opened_on) VALUES (1, CURRENT_DATE - 3), (2, CURRENT_DATE);
INSERT INTO examination_results (appointment_id, diagnosis_id, conclusion, recorded_on)
VALUES (1, 1, 'Учебная запись об осмотре', CURRENT_DATE - 2),
       (2, 2, 'Учебная запись о консультации', CURRENT_DATE - 1);
INSERT INTO invoices (appointment_id, issued_on) VALUES (1, CURRENT_DATE - 2), (2, CURRENT_DATE - 1);
UPDATE invoices SET status = 'paid', paid_on = CURRENT_DATE, payment_method = 'card' WHERE id = 1;
COMMIT;
