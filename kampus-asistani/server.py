"""
Kampüs Asistanı - Sıfırdan, bağımlılıksız bir MCP server.

MCP (Model Context Protocol) aslında şudur:
  Agent (istemci) <--- JSON-RPC mesajları (stdin/stdout) ---> Server (bu dosya)

Agent, server'a "hangi araçların var?" diye sorar (tools/list),
sonra ihtiyaç duyduğu aracı çağırır (tools/call). Hepsi bu kadar.

Çalıştırmak için sadece Python 3.8+ yeterli. pip install gerekmez.
"""

import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

# Windows'ta Türkçe karakterler bozulmasın diye giriş/çıkışı UTF-8 yapıyoruz.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # Log'lardaki emojiler Windows'ta hata vermesin

VERI = Path(__file__).parent / "veri"
GUNLER = ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar"]


# Log seviyesi: "detay" (varsayılan) gelen/giden her mesajı gösterir, "sessiz" sadece hataları.
LOG_SEVIYESI = os.getenv("KAMPUS_LOG", "detay")


def log(mesaj, onemli=False):
    """ÖNEMLİ: stdout sadece protokol mesajlarına ayrılmıştır.
    Kendi notlarımızı stderr'e yazarız, yoksa agent ile iletişim bozulur."""
    if LOG_SEVIYESI == "sessiz" and not onemli:
        return
    saat = datetime.now().strftime("%H:%M:%S")
    print(f"[{saat}] {mesaj}", file=sys.stderr, flush=True)


def kisalt(metin, uzunluk=90):
    metin = str(metin).replace("\n", " ⏎ ")
    return metin if len(metin) <= uzunluk else metin[:uzunluk] + "…"


def gelen_ozeti(mesaj):
    """Agent'tan gelen mesajı tek satırda özetler."""
    yontem = mesaj.get("method", "?")
    p = mesaj.get("params") or {}
    if yontem == "initialize":
        istemci = p.get("clientInfo", {})
        return f"initialize (istemci: {istemci.get('name', '?')} {istemci.get('version', '')})"
    if yontem == "tools/call":
        argumanlar = json.dumps(p.get("arguments") or {}, ensure_ascii=False)
        return f"tools/call → {p.get('name')}({argumanlar})"
    return yontem


def giden_ozeti(cevap):
    """Agent'a giden cevabı tek satırda özetler."""
    if "error" in cevap:
        return f"❌ HATA: {cevap['error']['message']}"
    sonuc = cevap["result"]
    if "tools" in sonuc:
        return f"{len(sonuc['tools'])} araç listelendi"
    if "serverInfo" in sonuc:
        return f"el sıkışıldı (protokol {sonuc['protocolVersion']})"
    if "content" in sonuc:
        etiket = "⚠️  araç hatası" if sonuc.get("isError") else "✅"
        return f"{etiket} {kisalt(sonuc['content'][0]['text'])}"
    return "boş cevap"


def oku(dosya):
    return json.loads((VERI / dosya).read_text(encoding="utf-8"))


def yaz(dosya, veri):
    (VERI / dosya).write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")


def gun_coz(gun):
    """'bugün', 'yarın' veya gün adını gerçek gün adına çevirir."""
    gun = (gun or "bugün").strip().lower()
    bugun = date.today().weekday()  # 0 = pazartesi
    if gun == "bugün":
        return GUNLER[bugun]
    if gun == "yarın":
        return GUNLER[(bugun + 1) % 7]
    if gun in GUNLER:
        return gun
    raise ValueError(f"Bilinmeyen gün: '{gun}'. Geçerli değerler: bugün, yarın, {', '.join(GUNLER)}")


# =====================================================================
# 1) ARAÇLAR (TOOLS): Agent'ın dış dünyada yapabildiği şeyler
# =====================================================================

def yemekhane_menusu(gun="bugün"):
    g = gun_coz(gun)
    return f"{g.capitalize()} menüsü: " + ", ".join(oku("menu.json")[g])


def ders_programi(gun="bugün"):
    g = gun_coz(gun)
    dersler = oku("ders_programi.json")[g]
    if not dersler:
        return f"{g.capitalize()} günü ders yok."
    satirlar = [f"- {d['saat']} {d['ders']} ({d['derslik']})" for d in dersler]
    return f"{g.capitalize()} dersleri:\n" + "\n".join(satirlar)


def odevleri_listele(sadece_bekleyenler=True):
    odevler = oku("odevler.json")
    if sadece_bekleyenler:
        odevler = [o for o in odevler if not o["tamamlandi"]]
    if not odevler:
        return "Bekleyen ödev yok. 🎉"
    return "\n".join(
        f"#{o['id']} [{'✓' if o['tamamlandi'] else ' '}] {o['ders']}: {o['aciklama']} (son tarih: {o['son_tarih']})"
        for o in odevler
    )


def odev_ekle(ders, aciklama, son_tarih):
    try:
        date.fromisoformat(son_tarih)
    except ValueError:
        raise ValueError(f"'{son_tarih}' geçerli bir tarih değil. YYYY-AA-GG biçiminde olmalı (örn. 2026-10-20).")
    odevler = oku("odevler.json")
    yeni = {
        "id": max((o["id"] for o in odevler), default=0) + 1,
        "ders": ders,
        "aciklama": aciklama,
        "son_tarih": son_tarih,
        "tamamlandi": False,
    }
    odevler.append(yeni)
    yaz("odevler.json", odevler)
    return f"Ödev #{yeni['id']} eklendi: {ders} - {aciklama} ({son_tarih})"


def odev_tamamla(id):
    odevler = oku("odevler.json")
    for o in odevler:
        if o["id"] == id:
            o["tamamlandi"] = True
            yaz("odevler.json", odevler)
            return f"Ödev #{id} tamamlandı olarak işaretlendi."
    raise ValueError(f"#{id} numaralı ödev bulunamadı.")


# Agent'a araçları TANITTIĞIMIZ yer. Agent, hangi aracı ne zaman
# kullanacağına bu açıklamalara (description) bakarak karar verir.
# Yani iyi bir açıklama yazmak, iyi bir kod yazmak kadar önemlidir!
GUN_PARAMETRESI = {
    "type": "string",
    "description": "'bugün', 'yarın' veya gün adı (örn. 'salı'). Varsayılan: bugün",
}

ARACLAR = {
    "yemekhane_menusu": {
        "fonksiyon": yemekhane_menusu,
        "description": "Kampüs yemekhanesinin belirtilen gündeki menüsünü getirir.",
        "inputSchema": {"type": "object", "properties": {"gun": GUN_PARAMETRESI}},
    },
    "ders_programi": {
        "fonksiyon": ders_programi,
        "description": "Öğrencinin belirtilen gündeki derslerini saat ve derslik bilgisiyle getirir.",
        "inputSchema": {"type": "object", "properties": {"gun": GUN_PARAMETRESI}},
    },
    "odevleri_listele": {
        "fonksiyon": odevleri_listele,
        "description": "Öğrencinin ödevlerini listeler. Varsayılan olarak yalnızca bitmemiş ödevleri gösterir.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sadece_bekleyenler": {
                    "type": "boolean",
                    "description": "true ise sadece tamamlanmamış ödevler gelir. Varsayılan: true",
                }
            },
        },
    },
    "odev_ekle": {
        "fonksiyon": odev_ekle,
        "description": "Ödev listesine yeni bir ödev ekler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ders": {"type": "string", "description": "Dersin adı"},
                "aciklama": {"type": "string", "description": "Ödevin kısa açıklaması"},
                "son_tarih": {"type": "string", "description": "Teslim tarihi, YYYY-AA-GG biçiminde (örn. 2026-10-20)"},
            },
            "required": ["ders", "aciklama", "son_tarih"],
        },
    },
    "odev_tamamla": {
        "fonksiyon": odev_tamamla,
        "description": "Numarası verilen ödevi tamamlandı olarak işaretler.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "Ödev numarası"}},
            "required": ["id"],
        },
    },
}


# =====================================================================
# 2) PROTOKOL: Agent'tan gelen mesajlara nasıl cevap verdiğimiz
# =====================================================================

def mesaji_isle(mesaj):
    yontem = mesaj.get("method")
    parametreler = mesaj.get("params") or {}

    # El sıkışma: "Merhaba, ben şu server'ım, araçlarım var."
    if yontem == "initialize":
        return {
            "protocolVersion": parametreler.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "kampus-asistani", "version": "1.0.0"},
        }

    # "Hangi araçların var?"
    if yontem == "tools/list":
        return {
            "tools": [
                {"name": ad, "description": a["description"], "inputSchema": a["inputSchema"]}
                for ad, a in ARACLAR.items()
            ]
        }

    # "Şu aracı şu parametrelerle çalıştır."
    if yontem == "tools/call":
        ad = parametreler.get("name")
        argumanlar = parametreler.get("arguments") or {}
        if ad not in ARACLAR:
            raise LookupError(f"Bilinmeyen araç: {ad}")
        try:
            eksik = [p for p in ARACLAR[ad]["inputSchema"].get("required", []) if p not in argumanlar]
            if eksik:
                raise ValueError(f"Eksik zorunlu parametre(ler): {', '.join(eksik)}")
            sonuc = ARACLAR[ad]["fonksiyon"](**argumanlar)
            return {"content": [{"type": "text", "text": sonuc}], "isError": False}
        except Exception as hata:
            # Araç hatalarını agent'a normal cevap olarak iletiyoruz;
            # böylece agent hatayı okuyup düzeltmeyi deneyebilir.
            return {"content": [{"type": "text", "text": f"Hata: {hata}"}], "isError": True}

    if yontem == "ping":
        return {}

    raise LookupError(f"Desteklenmeyen yöntem: {yontem}")


def main():
    log(f"🚀 Kampüs Asistanı server'ı başladı ({len(ARACLAR)} araç). Mesaj bekleniyor...")

    # Biri server'ı terminalden doğrudan çalıştırdıysa ona nasıl test edeceğini gösterelim.
    if sys.stdin.isatty():
        log("💡 Bu server bir agent'tan mesaj bekler. Elle denemek için şu satırı yapıştırıp Enter'a bas:", onemli=True)
        print('   {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"yemekhane_menusu","arguments":{"gun":"salı"}}}',
              file=sys.stderr, flush=True)
        log("   Çıkmak için Ctrl+C.", onemli=True)

    # Her satır bir JSON-RPC mesajıdır.
    for satir in sys.stdin:
        if not satir.strip():
            continue
        try:
            mesaj = json.loads(satir)
        except json.JSONDecodeError:
            log(f"❌ Geçersiz JSON atlandı: {kisalt(satir.strip())}", onemli=True)
            continue

        # 'id' alanı olmayan mesajlar bildirimdir (notification); cevap beklenmez.
        if "id" not in mesaj:
            log(f"🔔 BİLDİRİM: {mesaj.get('method')} (cevap gerekmez)")
            continue

        log(f"⬅️  GELEN  #{mesaj['id']}: {gelen_ozeti(mesaj)}")
        baslangic = time.perf_counter()

        cevap = {"jsonrpc": "2.0", "id": mesaj["id"]}
        try:
            cevap["result"] = mesaji_isle(mesaj)
        except LookupError as hata:
            cevap["error"] = {"code": -32601, "message": str(hata)}
        except Exception as hata:
            cevap["error"] = {"code": -32603, "message": str(hata)}

        sure = (time.perf_counter() - baslangic) * 1000
        log(f"➡️  GİDEN  #{mesaj['id']}: {giden_ozeti(cevap)} ({sure:.1f} ms)", onemli="error" in cevap)
        print(json.dumps(cevap, ensure_ascii=False), flush=True)

    log("👋 Agent bağlantıyı kapattı, server kapanıyor.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("👋 Ctrl+C ile kapatıldı.", onemli=True)
