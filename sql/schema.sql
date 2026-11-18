BEGIN;
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE DOMAIN nonblank AS text CHECK (btrim(VALUE) <> '');
CREATE DOMAIN russian_phone AS text CHECK (VALUE ~ '^[+]7[0-9]{10}$');
CREATE DOMAIN rubles AS numeric(12,2) CHECK (VALUE >= 0);
CREATE DOMAIN percent_value AS numeric(5,2) CHECK (VALUE BETWEEN 0 AND 100);
CREATE DOMAIN appointment_minutes AS smallint CHECK (VALUE BETWEEN 15 AND 720);

CREATE TABLE positions (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name nonblank NOT NULL,
    category nonblank NOT NULL,
    rate rubles NOT NULL,
    UNIQUE (name, category)
);
CREATE TABLE specializations (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code nonblank NOT NULL UNIQUE,
    name nonblank NOT NULL
);
CREATE TABLE benefit_categories (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name nonblank NOT NULL UNIQUE,
    discount_percent percent_value NOT NULL,
    confirmation_requirements nonblank NOT NULL
);
CREATE TABLE doctors (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    last_name nonblank NOT NULL,
    first_name nonblank NOT NULL,
    middle_name nonblank,
    birth_date date NOT NULL CHECK (birth_date <= CURRENT_DATE),
    phone russian_phone NOT NULL,
    diploma_number nonblank NOT NULL UNIQUE,
    position_id integer NOT NULL REFERENCES positions(id) ON DELETE RESTRICT,
    specialization_id integer NOT NULL REFERENCES specializations(id) ON DELETE RESTRICT
);
CREATE TABLE patients (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    last_name nonblank NOT NULL,
    first_name nonblank NOT NULL,
    middle_name nonblank,
    sex text NOT NULL CHECK (sex IN ('F','M')),
    birth_date date NOT NULL CHECK (birth_date <= CURRENT_DATE),
    address nonblank NOT NULL,
    phone russian_phone NOT NULL,
    oms text UNIQUE CHECK (oms ~ '^[0-9]{16}$'),
    snils text UNIQUE CHECK (snils ~ '^[0-9]{11}$'),
    passport_series text CHECK (passport_series ~ '^[0-9]{4}$'),
    passport_number text CHECK (passport_number ~ '^[0-9]{6}$'),
    login nonblank NOT NULL UNIQUE,
    password_hash nonblank NOT NULL,
    benefit_category_id integer REFERENCES benefit_categories(id) ON DELETE RESTRICT,
    benefit_document nonblank,
    UNIQUE (passport_series, passport_number),
    CONSTRAINT benefit_confirmed CHECK ((benefit_category_id IS NULL) = (benefit_document IS NULL)),
    CONSTRAINT passport_complete CHECK ((passport_series IS NULL) = (passport_number IS NULL))
);
CREATE TABLE services (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code nonblank NOT NULL UNIQUE,
    name nonblank NOT NULL,
    price rubles NOT NULL,
    duration_minutes appointment_minutes NOT NULL
);
CREATE TABLE diagnoses (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    icd_code nonblank NOT NULL UNIQUE,
    name nonblank NOT NULL
);
CREATE TABLE appointments (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id integer NOT NULL REFERENCES patients(id) ON DELETE RESTRICT,
    doctor_id integer NOT NULL REFERENCES doctors(id) ON DELETE RESTRICT,
    service_id integer NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
    visit_date date NOT NULL,
    visit_time time(0) NOT NULL,
    booked_minutes appointment_minutes NOT NULL,
    reason nonblank NOT NULL,
    status text NOT NULL DEFAULT 'created'
        CHECK (status IN ('created','confirmed','completed','cancelled')),
    CONSTRAINT doctor_overlap EXCLUDE USING gist (
        doctor_id WITH =,
        tsrange(visit_date + visit_time,
                visit_date + visit_time + booked_minutes * interval '1 minute', '[)') WITH &&
    ) WHERE (status <> 'cancelled'),
    CONSTRAINT patient_overlap EXCLUDE USING gist (
        patient_id WITH =,
        tsrange(visit_date + visit_time,
                visit_date + visit_time + booked_minutes * interval '1 minute', '[)') WITH &&
    ) WHERE (status <> 'cancelled')
);
CREATE TABLE medical_cards (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id integer NOT NULL UNIQUE REFERENCES patients(id) ON DELETE RESTRICT,
    opened_on date NOT NULL DEFAULT CURRENT_DATE,
    updated_on date NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT card_dates CHECK (opened_on <= updated_on)
);
CREATE TABLE examination_results (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    appointment_id integer NOT NULL UNIQUE REFERENCES appointments(id) ON DELETE RESTRICT,
    diagnosis_id integer REFERENCES diagnoses(id) ON DELETE RESTRICT,
    findings nonblank,
    conclusion nonblank NOT NULL,
    prescriptions nonblank,
    recorded_on date NOT NULL DEFAULT CURRENT_DATE
);
CREATE TABLE invoices (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    appointment_id integer NOT NULL UNIQUE REFERENCES appointments(id) ON DELETE RESTRICT,
    base_price rubles NOT NULL,
    discount_percent percent_value NOT NULL,
    status text NOT NULL DEFAULT 'issued' CHECK (status IN ('issued','paid','cancelled')),
    issued_on date NOT NULL DEFAULT CURRENT_DATE,
    paid_on date,
    payment_method text CHECK (payment_method IN ('cash','card','transfer')),
    CONSTRAINT payment_details CHECK (
        (status = 'paid' AND paid_on IS NOT NULL AND payment_method IS NOT NULL AND paid_on >= issued_on)
        OR (status <> 'paid' AND paid_on IS NULL AND payment_method IS NULL)
    )
);

CREATE VIEW invoice_totals AS
SELECT id, round(base_price * discount_percent / 100, 2) AS discount_amount,
       base_price - round(base_price * discount_percent / 100, 2) AS total
FROM invoices;

CREATE FUNCTION guard_appointment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'created' THEN
            RAISE EXCEPTION 'Новая запись должна иметь статус «Создана».' USING ERRCODE = '23514';
        END IF;
        SELECT duration_minutes INTO NEW.booked_minutes FROM services WHERE id = NEW.service_id;
    ELSE
        IF NEW.status <> OLD.status AND NOT (
            (OLD.status = 'created' AND NEW.status IN ('confirmed','cancelled'))
            OR (OLD.status = 'confirmed' AND NEW.status IN ('completed','cancelled'))
        ) THEN
            RAISE EXCEPTION 'Недопустимый переход статуса записи.' USING ERRCODE = '23514';
        END IF;
        IF (NEW.patient_id, NEW.doctor_id, NEW.service_id, NEW.visit_date, NEW.visit_time)
           IS DISTINCT FROM
           (OLD.patient_id, OLD.doctor_id, OLD.service_id, OLD.visit_date, OLD.visit_time)
           AND (OLD.status IN ('completed','cancelled') OR NEW.status IN ('completed','cancelled')) THEN
            RAISE EXCEPTION 'Завершённую или отменённую запись нельзя переносить.' USING ERRCODE = '23514';
        END IF;
        NEW.booked_minutes := OLD.booked_minutes;
        IF NEW.service_id <> OLD.service_id THEN
            SELECT duration_minutes INTO NEW.booked_minutes FROM services WHERE id = NEW.service_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER appointments_rules BEFORE INSERT OR UPDATE ON appointments
FOR EACH ROW EXECUTE FUNCTION guard_appointment();

CREATE FUNCTION guard_exam() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    visit appointments%ROWTYPE;
BEGIN
    SELECT * INTO visit FROM appointments WHERE id = NEW.appointment_id;
    IF visit.status IS DISTINCT FROM 'completed' THEN
        RAISE EXCEPTION 'Выберите завершённый приём.' USING ERRCODE = '23514';
    END IF;
    IF NEW.recorded_on < visit.visit_date THEN
        RAISE EXCEPTION 'Дата результата не может предшествовать приёму.' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER examination_rules BEFORE INSERT OR UPDATE ON examination_results
FOR EACH ROW EXECUTE FUNCTION guard_exam();

CREATE FUNCTION guard_invoice() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    visit appointments%ROWTYPE;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'issued' THEN
            RAISE EXCEPTION 'Новый счёт должен иметь статус «Выставлен».' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.status <> OLD.status AND NOT (OLD.status = 'issued' AND NEW.status IN ('paid','cancelled')) THEN
            RAISE EXCEPTION 'Недопустимый переход статуса счёта.' USING ERRCODE = '23514';
        END IF;
        IF OLD.status IN ('paid','cancelled') AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'Оплаченный или отменённый счёт закрыт для изменения.' USING ERRCODE = '23514';
        END IF;
        IF NEW.appointment_id <> OLD.appointment_id AND NEW.status <> 'issued' THEN
            RAISE EXCEPTION 'Изменение приёма и закрытие счёта выполняются отдельно.' USING ERRCODE = '23514';
        END IF;
        NEW.base_price := OLD.base_price;
        NEW.discount_percent := OLD.discount_percent;
    END IF;
    SELECT * INTO visit FROM appointments WHERE id = NEW.appointment_id;
    IF visit.status IS DISTINCT FROM 'completed' THEN
        RAISE EXCEPTION 'Счёт выставляется по завершённому приёму.' USING ERRCODE = '23514';
    END IF;
    IF NEW.issued_on < visit.visit_date THEN
        RAISE EXCEPTION 'Дата выставления счёта не может предшествовать приёму.' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT s.price, COALESCE(b.discount_percent, 0)
        INTO NEW.base_price, NEW.discount_percent
        FROM services s CROSS JOIN patients p
        LEFT JOIN benefit_categories b ON b.id = p.benefit_category_id
        WHERE s.id = visit.service_id AND p.id = visit.patient_id;
    ELSIF NEW.appointment_id <> OLD.appointment_id THEN
        SELECT s.price, COALESCE(b.discount_percent, 0)
        INTO NEW.base_price, NEW.discount_percent
        FROM services s CROSS JOIN patients p
        LEFT JOIN benefit_categories b ON b.id = p.benefit_category_id
        WHERE s.id = visit.service_id AND p.id = visit.patient_id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER invoice_rules BEFORE INSERT OR UPDATE ON invoices
FOR EACH ROW EXECUTE FUNCTION guard_invoice();
COMMIT;
