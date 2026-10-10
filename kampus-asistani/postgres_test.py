"""
PostgreSQL MCP server'ını gerçek veritabanına karşı tek komutla test eder.

Önce:   docker compose up -d
Sonra:  python postgres_test.py

Her aracı MCP üzerinden (ajanın kullandığı yolla) çağırır ve sonucu ✅ / ❌ olarak gösterir.
Test sırasında eklenen ödev en sonda silinir; veritabanı eski haline döner.
"""

import asyncio
import sys

import psycopg
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

sys.stdout.reconfigure(encoding="utf-8")

YAZAR_DSN = "postgresql://kampus:kampus123@localhost:5433/kampus"
basarili = basarisiz = 0


def metin(sonuc):
    return "\n".join(b.get("text", "") for b in sonuc) if isinstance(sonuc, list) else str(sonuc)


def kontrol(ad, sonuc, beklenen, hata_bekleniyor=False):
    global basarili, basarisiz
    t = metin(sonuc)
    hata_var = t.startswith("Error executing tool")
    tamam = beklenen in t and hata_var == hata_bekleniyor
    basarili, basarisiz = basarili + tamam, basarisiz + (not tamam)
    print(f"{'✅' if tamam else '❌'} {ad}\n   {t.replace(chr(10), chr(10) + '   ')}\n")


async def main():
    print("1) Veritabanı bağlantısı...")
    try:
        with psycopg.connect(YAZAR_DSN, connect_timeout=3) as b:
            surum = b.execute("SHOW server_version").fetchone()[0]
        print(f"✅ PostgreSQL {surum} çalışıyor.\n")
    except psycopg.OperationalError as hata:
        print(f"❌ Bağlanılamadı: {hata}\n   'docker compose up -d' ve 'docker compose ps' ile kontrol et.")
        return

    print("2) MCP araçları...\n")
    istemci = MultiServerMCPClient({"pg": {
        "transport": "stdio", "command": sys.executable, "args": ["server_postgres.py"],
        "env": {"KAMPUS_LOG": "sessiz"},
    }})
    async with istemci.session("pg") as oturum:
        a = {t.name: t for t in await load_mcp_tools(oturum)}

        kontrol("yemekhane_menusu", await a["yemekhane_menusu"].ainvoke({"gun": "salı"}), "Ezogelin")
        kontrol("ders_programi", await a["ders_programi"].ainvoke({"gun": "çarşamba"}), "Algoritmalar")
        kontrol("odevleri_listele", await a["odevleri_listele"].ainvoke({}), "Quicksort")
        kontrol("odevleri_listele (hepsi)", await a["odevleri_listele"].ainvoke({"sadece_bekleyenler": False}), "JOIN")

        eklenen = metin(await a["odev_ekle"].ainvoke(
            {"ders": "algoritmalar", "aciklama": "TEST ödevi", "son_tarih": "2026-12-31"}))
        kontrol("odev_ekle", eklenen, "eklendi")
        yeni_id = int(eklenen.split("#")[1].split()[0]) if "#" in eklenen else -1
        kontrol("odev_tamamla", await a["odev_tamamla"].ainvoke({"id": yeni_id}), "tamamlandı")
        kontrol("odev_ekle (olmayan ders → hata beklenir)",
                await a["odev_ekle"].ainvoke({"ders": "Fizik", "aciklama": "x", "son_tarih": "2026-12-31"}),
                "Mevcut dersler", hata_bekleniyor=True)

        kontrol("sql_sorgula", await a["sql_sorgula"].ainvoke(
            {"sorgu": "SELECT gun, sum(kalori) AS toplam FROM menu WHERE gun = 'perşembe' GROUP BY gun"}),
            "890")
        kontrol("sql_sorgula (SQL hatası → hata beklenir)",
                await a["sql_sorgula"].ainvoke({"sorgu": "SELECT olmayan_sutun FROM menu"}),
                "SQL hatası", hata_bekleniyor=True)
        # 'WITH' ile başlayan gizli bir silme: 1. katmanı geçer, salt okunur katmanlar durdurmalı.
        kontrol("sql_sorgula (gizli DELETE → engellenmeli)",
                await a["sql_sorgula"].ainvoke({"sorgu": "WITH s AS (DELETE FROM odevler RETURNING *) SELECT count(*) FROM s"}),
                "SQL hatası", hata_bekleniyor=True)

    # Test ödevini temizle.
    with psycopg.connect(YAZAR_DSN) as b:
        b.execute("DELETE FROM odevler WHERE aciklama = 'TEST ödevi'")
        kalan = b.execute("SELECT count(*) FROM odevler").fetchone()[0]
    print(f"🧹 Test ödevi silindi (tabloda {kalan} ödev var, 5 olmalı).\n")
    print(f"Sonuç: {basarili} başarılı, {basarisiz} başarısız")


asyncio.run(main())
