-- Kampüs Asistanı veritabanı şeması ve örnek veriler.
-- Docker bu dosyayı veritabanı İLK KEZ oluşturulurken otomatik çalıştırır.

-- ---------------------------------------------------------------
-- TABLOLAR
-- ---------------------------------------------------------------

CREATE TABLE dersler (
    id            SERIAL PRIMARY KEY,
    ad            TEXT NOT NULL UNIQUE,
    ogretim_uyesi TEXT NOT NULL,
    kredi         INT  NOT NULL CHECK (kredi > 0)
);

CREATE TABLE ders_programi (
    id      SERIAL PRIMARY KEY,
    ders_id INT  NOT NULL REFERENCES dersler(id),
    gun     TEXT NOT NULL CHECK (gun IN ('pazartesi','salı','çarşamba','perşembe','cuma','cumartesi','pazar')),
    saat    TIME NOT NULL,
    derslik TEXT NOT NULL
);

CREATE TABLE menu (
    id     SERIAL PRIMARY KEY,
    gun    TEXT NOT NULL CHECK (gun IN ('pazartesi','salı','çarşamba','perşembe','cuma','cumartesi','pazar')),
    sira   INT  NOT NULL,
    yemek  TEXT NOT NULL,
    kalori INT
);

CREATE TABLE odevler (
    id         SERIAL PRIMARY KEY,
    ders_id    INT  NOT NULL REFERENCES dersler(id),
    aciklama   TEXT NOT NULL,
    son_tarih  DATE NOT NULL,
    tamamlandi BOOLEAN NOT NULL DEFAULT false,
    eklenme    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------
-- ÖRNEK VERİLER
-- ---------------------------------------------------------------

INSERT INTO dersler (ad, ogretim_uyesi, kredi) VALUES
    ('Algoritmalar',          'Dr. Ayşe Yılmaz',    4),
    ('Veritabanı Sistemleri', 'Dr. Mehmet Kaya',    3),
    ('Yapay Zekâya Giriş',    'Doç. Dr. Elif Demir', 3),
    ('İşletim Sistemleri',    'Dr. Can Öztürk',     4);

INSERT INTO ders_programi (ders_id, gun, saat, derslik) VALUES
    (1, 'pazartesi', '09:00', 'B-201'),
    (2, 'pazartesi', '13:00', 'Lab-3'),
    (3, 'salı',      '10:00', 'A-105'),
    (1, 'çarşamba',  '09:00', 'B-201'),
    (4, 'çarşamba',  '15:00', 'C-302'),
    (3, 'perşembe',  '11:00', 'A-105'),
    (2, 'cuma',      '14:00', 'Lab-3');

INSERT INTO menu (gun, sira, yemek, kalori) VALUES
    ('pazartesi', 1, 'Mercimek çorbası', 180), ('pazartesi', 2, 'Tavuk sote', 350),
    ('pazartesi', 3, 'Pilav', 300),            ('pazartesi', 4, 'Ayran', 80),
    ('salı', 1, 'Ezogelin çorbası', 170),      ('salı', 2, 'Kuru fasulye', 320),
    ('salı', 3, 'Bulgur pilavı', 280),         ('salı', 4, 'Turşu', 20),
    ('çarşamba', 1, 'Domates çorbası', 150),   ('çarşamba', 2, 'Karnıyarık', 400),
    ('çarşamba', 3, 'Makarna', 350),           ('çarşamba', 4, 'Sütlaç', 250),
    ('perşembe', 1, 'Yayla çorbası', 160),     ('perşembe', 2, 'Izgara köfte', 450),
    ('perşembe', 3, 'Patates püresi', 220),    ('perşembe', 4, 'Salata', 60),
    ('cuma', 1, 'Tarhana çorbası', 140),       ('cuma', 2, 'Fırın tavuk', 380),
    ('cuma', 3, 'Pilav', 300),                 ('cuma', 4, 'Revani', 330);

INSERT INTO odevler (ders_id, aciklama, son_tarih, tamamlandi) VALUES
    (1, 'Quicksort raporu',              '2026-10-15', false),
    (2, 'ER diyagramı çizimi',           '2026-10-13', false),
    (2, 'SQL JOIN alıştırmaları',        '2026-10-08', true),
    (3, 'Arama algoritmaları ödevi',     '2026-10-22', false),
    (4, 'Süreç zamanlama simülasyonu',   '2026-10-29', false);

-- ---------------------------------------------------------------
-- GÜVENLİK: Ajanın serbest SQL sorguları için SADECE OKUMA yetkili kullanıcı
-- ("En az yetki ilkesi": LLM'in yazdığı SQL'e asla tam yetki verme!)
-- ---------------------------------------------------------------

CREATE ROLE ajan_okuyucu LOGIN PASSWORD 'okuyucu123';
GRANT CONNECT ON DATABASE kampus TO ajan_okuyucu;
GRANT USAGE ON SCHEMA public TO ajan_okuyucu;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ajan_okuyucu;
