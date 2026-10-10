"""
Kampüs Asistanı - PostgreSQL MCP server'ı.

server.py'de protokolü elle yazmıştık. Burada aynı işi resmi MCP SDK'sındaki
FastMCP ile yapıyoruz: her araç sadece bir Python fonksiyonu + @mcp.tool() dekoratörü.
Fonksiyon adı, açıklaması (docstring) ve parametre tipleri otomatik olarak
MCP araç tanımına dönüştürülür.

Araç adları server.py ile AYNI. Bu sayede ajan hiç değişmeden iki server ile de çalışır.
Ek olarak 'sql_sorgula' aracı, ajanın kendi SQL sorgusunu yazmasına izin verir.

Önce veritabanını başlat:  docker compose up -d
"""

import os
import sys
import time
from datetime import date, datetime
from typing import Annotated

import psycopg
from mcp.server.fastmcp import FastMCP
from psycopg.rows import dict_row
from pydantic import Field

# Windows'ta Türkçe karakterler ve emojiler log'larda hata vermesin.
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Bağlantı bilgileri (docker-compose.yml ve init.sql ile uyumlu varsayılanlar).
YAZAR_DSN = os.getenv("KAMPUS_DB", "postgresql://kampus:kampus123@localhost:5433/kampus")
OKUYUCU_DSN = os.getenv("KAMPUS_DB_OKUYUCU", "postgresql://ajan_okuyucu:okuyucu123@localhost:5433/kampus")
LOG_SEVIYESI = os.getenv("KAMPUS_LOG", "detay")

GUNLER = ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar"]

mcp = FastMCP("kampus-postgres", log_level="WARNING")


def log(mesaj, onemli=False):
    """stdout protokole ayrılmıştır; loglar stderr'e gider."""
    if LOG_SEVIYESI == "sessiz" and not onemli:
        return
    print(f"[{datetime.now():%H:%M:%S}] {mesaj}", file=sys.stderr, flush=True)


def sorgula(sql, parametreler=(), salt_okunur=False):
    """Veritabanında bir sorgu çalıştırır ve satırları sözlük listesi olarak döndürür.

    salt_okunur=True ise iki güvenlik katmanı devreye girer:
      1) Sadece SELECT yetkisi olan 'ajan_okuyucu' kullanıcısıyla bağlanılır.
      2) Bağlantı READ ONLY moduna alınır ve sorguya 5 saniye süre sınırı konur.
    """
    dsn = OKUYUCU_DSN if salt_okunur else YAZAR_DSN
    tek_satir_sql = " ".join(sql.split())
    log(f"🐘 SQL{' (salt okunur)' if salt_okunur else ''}: {tek_satir_sql}  {list(parametreler) or ''}")
    baslangic = time.perf_counter()
    try:
        with psycopg.connect(dsn, connect_timeout=3, options="-c statement_timeout=5000") as baglanti:
            baglanti.read_only = salt_okunur
            with baglanti.cursor(row_factory=dict_row) as imlec:
                imlec.execute(sql, parametreler)
                satirlar = imlec.fetchmany(50) if imlec.description else []
    except psycopg.OperationalError as hata:
        log(f"❌ Veritabanı bağlantı hatası: {hata}", onemli=True)
        raise RuntimeError(
            "Veritabanına bağlanılamadı. PostgreSQL çalışıyor mu? "
            "Proje klasöründe 'docker compose up -d' komutunu çalıştır."
        ) from hata
    log(f"🐘 {len(satirlar)} satır döndü ({(time.perf_counter() - baslangic) * 1000:.1f} ms)")
    return satirlar


def gun_coz(gun):
    gun = (gun or "bugün").strip().lower()
    bugun = date.today().weekday()
    if gun == "bugün":
        return GUNLER[bugun]
    if gun == "yarın":
        return GUNLER[(bugun + 1) % 7]
    if gun in GUNLER:
        return gun
    raise ValueError(f"Bilinmeyen gün: '{gun}'. Geçerli değerler: bugün, yarın, {', '.join(GUNLER)}")


GunParametresi = Annotated[str, Field(description="'bugün', 'yarın' veya gün adı (örn. 'salı'). Varsayılan: bugün")]


# =====================================================================
# ARAÇLAR (server.py ile aynı adlar ve aynı davranış)
# =====================================================================

@mcp.tool()
def yemekhane_menusu(gun: GunParametresi = "bugün") -> str:
    """Kampüs yemekhanesinin belirtilen gündeki menüsünü kalori bilgisiyle getirir."""
    log(f"🔧 yemekhane_menusu(gun={gun!r})")
    g = gun_coz(gun)
    satirlar = sorgula("SELECT yemek, kalori FROM menu WHERE gun = %s ORDER BY sira", (g,))
    if not satirlar:
        return f"{g.capitalize()} günü yemekhane kapalı."
    toplam = sum(s["kalori"] or 0 for s in satirlar)
    yemekler = ", ".join(f"{s['yemek']} ({s['kalori']} kcal)" for s in satirlar)
    return f"{g.capitalize()} menüsü: {yemekler}. Toplam: {toplam} kcal"


@mcp.tool()
def ders_programi(gun: GunParametresi = "bugün") -> str:
    """Öğrencinin belirtilen gündeki derslerini saat, derslik ve öğretim üyesi bilgisiyle getirir."""
    log(f"🔧 ders_programi(gun={gun!r})")
    g = gun_coz(gun)
    satirlar = sorgula(
        """SELECT to_char(p.saat, 'HH24:MI') AS saat, d.ad, p.derslik, d.ogretim_uyesi
           FROM ders_programi p JOIN dersler d ON d.id = p.ders_id
           WHERE p.gun = %s ORDER BY p.saat""",
        (g,),
    )
    if not satirlar:
        return f"{g.capitalize()} günü ders yok."
    return f"{g.capitalize()} dersleri:\n" + "\n".join(
        f"- {s['saat']} {s['ad']} ({s['derslik']}, {s['ogretim_uyesi']})" for s in satirlar
    )


@mcp.tool()
def odevleri_listele(
    sadece_bekleyenler: Annotated[bool, Field(description="true ise sadece tamamlanmamış ödevler gelir")] = True,
) -> str:
    """Öğrencinin ödevlerini son tarihe göre sıralı listeler. Varsayılan olarak yalnızca bitmemiş ödevleri gösterir."""
    log(f"🔧 odevleri_listele(sadece_bekleyenler={sadece_bekleyenler})")
    satirlar = sorgula(
        """SELECT o.id, d.ad AS ders, o.aciklama, o.son_tarih, o.tamamlandi
           FROM odevler o JOIN dersler d ON d.id = o.ders_id
           WHERE NOT o.tamamlandi OR NOT %s
           ORDER BY o.son_tarih""",
        (sadece_bekleyenler,),
    )
    if not satirlar:
        return "Bekleyen ödev yok. 🎉"
    return "\n".join(
        f"#{s['id']} [{'✓' if s['tamamlandi'] else ' '}] {s['ders']}: {s['aciklama']} (son tarih: {s['son_tarih']})"
        for s in satirlar
    )


@mcp.tool()
def odev_ekle(
    ders: Annotated[str, Field(description="Dersin adı (örn. 'Algoritmalar')")],
    aciklama: Annotated[str, Field(description="Ödevin kısa açıklaması")],
    son_tarih: Annotated[str, Field(description="Teslim tarihi, YYYY-AA-GG biçiminde (örn. 2026-10-20)")],
) -> str:
    """Ödev listesine yeni bir ödev ekler. Ders adı veritabanındaki derslerden biri olmalıdır."""
    log(f"🔧 odev_ekle(ders={ders!r}, aciklama={aciklama!r}, son_tarih={son_tarih!r})")
    try:
        date.fromisoformat(son_tarih)
    except ValueError:
        raise ValueError(f"'{son_tarih}' geçerli bir tarih değil. YYYY-AA-GG biçiminde olmalı (örn. 2026-10-20).")

    bulunan = sorgula("SELECT id, ad FROM dersler WHERE lower(ad) = lower(%s)", (ders.strip(),))
    if not bulunan:
        tum_dersler = ", ".join(s["ad"] for s in sorgula("SELECT ad FROM dersler ORDER BY ad"))
        raise ValueError(f"'{ders}' adında bir ders yok. Mevcut dersler: {tum_dersler}")

    yeni = sorgula(
        "INSERT INTO odevler (ders_id, aciklama, son_tarih) VALUES (%s, %s, %s) RETURNING id",
        (bulunan[0]["id"], aciklama, son_tarih),
    )[0]
    return f"Ödev #{yeni['id']} eklendi: {bulunan[0]['ad']} - {aciklama} ({son_tarih})"


@mcp.tool()
def odev_tamamla(id: Annotated[int, Field(description="Ödev numarası")]) -> str:
    """Numarası verilen ödevi tamamlandı olarak işaretler."""
    log(f"🔧 odev_tamamla(id={id})")
    satirlar = sorgula("UPDATE odevler SET tamamlandi = true WHERE id = %s RETURNING id", (id,))
    if not satirlar:
        raise ValueError(f"#{id} numaralı ödev bulunamadı.")
    return f"Ödev #{id} tamamlandı olarak işaretlendi."


# =====================================================================
# YENİ: Ajanın kendi SQL sorgusunu yazdığı araç
# =====================================================================

@mcp.tool()
def sql_sorgula(sorgu: Annotated[str, Field(description="Çalıştırılacak tek bir SELECT sorgusu")]) -> str:
    """Kampüs veritabanında SALT OKUNUR bir SQL (PostgreSQL) sorgusu çalıştırır.
    Diğer araçlarla cevaplanamayan sorular için kullan: istatistik, sayma, toplama,
    karşılaştırma, öğretim üyesi veya kredi bilgisi gibi. En fazla 50 satır döner.

    Şema:
      dersler(id, ad, ogretim_uyesi, kredi)
      ders_programi(id, ders_id -> dersler.id, gun, saat, derslik)
      menu(id, gun, sira, yemek, kalori)
      odevler(id, ders_id -> dersler.id, aciklama, son_tarih DATE, tamamlandi BOOLEAN, eklenme)
    'gun' sütunları küçük harfli Türkçe gün adlarıdır: 'pazartesi', 'salı', 'çarşamba', ...
    """
    log(f"🔧 sql_sorgula(sorgu={sorgu!r})")
    temiz = sorgu.strip().rstrip(";")
    # 1. katman: Sadece SELECT/WITH ile başlayan tek bir sorguya izin ver.
    if not temiz.lower().startswith(("select", "with")) or ";" in temiz:
        raise ValueError("Sadece tek bir SELECT sorgusu çalıştırılabilir.")
    # 2. ve 3. katman: salt okunur kullanıcı + READ ONLY bağlantı (bkz. sorgula()).
    try:
        satirlar = sorgula(temiz, salt_okunur=True)
    except psycopg.Error as hata:
        # SQL hatasını ajana geri ver; böylece sorgusunu düzeltip tekrar deneyebilir.
        raise ValueError(f"SQL hatası: {hata}".strip())
    if not satirlar:
        return "Sorgu sonuç döndürmedi."
    basliklar = list(satirlar[0].keys())
    tablo = [" | ".join(basliklar)] + [" | ".join(str(s[b]) for b in basliklar) for s in satirlar]
    return "\n".join(tablo)


if __name__ == "__main__":
    log(f"🚀 Kampüs Asistanı PostgreSQL server'ı başladı. Veritabanı: {YAZAR_DSN.split('@')[-1]}")
    mcp.run()  # Varsayılan taşıma: stdio (server.py ile aynı)
