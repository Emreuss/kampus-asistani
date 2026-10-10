"""
Kampüs Duyuru Servisi - Ağ üzerinden çalışan (uzak) bir MCP server.

Önceki server'lar öğrencinin kendi bilgisayarında, ajan tarafından alt süreç olarak
başlatılıyordu (stdio). Bu server ise EĞİTMENİN bilgisayarında bağımsız bir web servisi
olarak çalışır; öğrencilerin ajanları ona ağ üzerinden HTTP ile bağlanır
(MCP'nin "Streamable HTTP" taşıma katmanı). Gerçek dünyadaki GitHub, Notion gibi
uzak MCP server'lar da böyle çalışır.

Eğitmen:   python duyuru_server.py
           Tarayıcıda http://localhost:8000/panel adresini aç (sadece bu bilgisayardan açılır).
Öğrenci:   python ajan.py --kaynak postgres --duyuru http://<eğitmenin-ip>:8000/mcp --ad "Adın"
"""

import json
import os
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PORT = int(os.getenv("DUYURU_PORT", "8000"))
VERI_DOSYASI = Path(__file__).parent / "duyuru_veri.json"

# host="0.0.0.0": Sadece bu bilgisayardan değil, aynı ağdaki herkesten bağlantı kabul et.
mcp = FastMCP("kampus-duyuru", host="0.0.0.0", port=PORT, log_level="WARNING")


# =====================================================================
# VERİ: Duyurular, sorular ve anket. Server kapanınca kaybolmasın diye dosyaya da yazılır.
# =====================================================================

def veriyi_yukle():
    if VERI_DOSYASI.exists():
        return json.loads(VERI_DOSYASI.read_text(encoding="utf-8"))
    return {
        "duyurular": [{
            "id": 1, "tarih": datetime.now().strftime("%d.%m.%Y"), "saat": datetime.now().strftime("%H:%M"), "ders": "Genel", "tur": "genel",
            "metin": "Kampüs Duyuru Servisi'ne hoş geldiniz! Duyurular burada yayınlanacak.",
        }],
        "sorular": [],
        "anket": None,
    }


VERI = veriyi_yukle()


def kaydet():
    VERI_DOSYASI.write_text(json.dumps(VERI, ensure_ascii=False, indent=2), encoding="utf-8")


def log(mesaj):
    print(f"[{datetime.now():%H:%M:%S}] {mesaj}", file=sys.stderr, flush=True)


# =====================================================================
# MCP ARAÇLARI: Öğrencilerin ajanları bunları çağırır.
# =====================================================================

@mcp.tool()
def duyurulari_getir(
    ders: Annotated[str, Field(description="Sadece bu dersin duyuruları (boş bırakılırsa hepsi)")] = "",
) -> str:
    """Eğitmenin yayınladığı güncel kampüs duyurularını getirir: ders iptalleri, derslik
    değişiklikleri ve genel duyurular. Ders programı sorulduğunda iptal veya değişiklik
    olup olmadığını görmek için MUTLAKA bunu da kontrol et."""
    log(f"📢 duyurulari_getir(ders={ders!r})")
    duyurular = VERI["duyurular"]
    if ders.strip():
        aranan = ders.strip().lower()
        duyurular = [d for d in duyurular if aranan in d["ders"].lower() or d["ders"] == "Genel"]
    if not duyurular:
        return "Şu an yayında duyuru yok."
    etiket = {"iptal": "❌ İPTAL", "degisiklik": "🔄 DEĞİŞİKLİK", "genel": "📢 GENEL"}
    return "\n".join(
        f"[{d.get('tarih', '')} {d['saat']}] {etiket.get(d['tur'], '📢')} | {d['ders']}: {d['metin']}" for d in reversed(duyurular)
    )


@mcp.tool()
def soru_sor(
    soru: Annotated[str, Field(description="Eğitmene iletilecek soru")],
    ogrenci_adi: Annotated[str, Field(description="Soruyu soran öğrencinin adı")],
) -> str:
    """Öğrencinin sorusunu eğitmene iletir. Soru, eğitmenin ekranında canlı olarak görünür."""
    log(f"❓ soru_sor(ogrenci_adi={ogrenci_adi!r}, soru={soru!r})")
    VERI["sorular"].append({
        "id": len(VERI["sorular"]) + 1, "saat": datetime.now().strftime("%H:%M"),
        "ogrenci": ogrenci_adi.strip() or "Anonim", "soru": soru.strip(),
    })
    kaydet()
    return "Sorun eğitmene iletildi."


@mcp.tool()
def aktif_anket() -> str:
    """Eğitmenin başlattığı aktif anketi ve seçeneklerini getirir."""
    log("📊 aktif_anket()")
    anket = VERI["anket"]
    if not anket or not anket["acik"]:
        return "Şu an aktif bir anket yok."
    secenekler = "\n".join(f"{i}. {s}" for i, s in enumerate(anket["secenekler"], 1))
    return f"Anket: {anket['soru']}\nSeçenekler:\n{secenekler}\n(Toplam {len(anket['oylar'])} oy kullanıldı.)"


@mcp.tool()
def anket_oyu_ver(
    secenek: Annotated[str, Field(description="Seçeneğin kendisi veya numarası (örn. '2')")],
    ogrenci_adi: Annotated[str, Field(description="Oy veren öğrencinin adı")],
) -> str:
    """Aktif ankete oy verir. Her öğrencinin bir oyu vardır; tekrar oy verirse eski oyu değişir."""
    log(f"🗳️  anket_oyu_ver(ogrenci_adi={ogrenci_adi!r}, secenek={secenek!r})")
    anket = VERI["anket"]
    if not anket or not anket["acik"]:
        raise ValueError("Şu an aktif bir anket yok.")
    secenekler = anket["secenekler"]
    s = secenek.strip()
    if s.isdigit() and 1 <= int(s) <= len(secenekler):
        secilen = secenekler[int(s) - 1]
    else:
        eslesen = [x for x in secenekler if x.lower() == s.lower()]
        if not eslesen:
            raise ValueError(f"'{secenek}' geçerli bir seçenek değil. Seçenekler: {', '.join(secenekler)}")
        secilen = eslesen[0]
    anket["oylar"][ogrenci_adi.strip() or "Anonim"] = secilen
    kaydet()
    return f"Oyun kaydedildi: {secilen}"


# =====================================================================
# EĞİTMEN PANELİ: Normal web sayfaları (MCP değil). Sadece bu bilgisayardan erişilebilir.
# =====================================================================

def sadece_yerel(istek: Request):
    """Panel öğrencilere açık olmasın: sadece bu bilgisayardan gelen isteklere izin ver."""
    return istek.client and istek.client.host in ("127.0.0.1", "::1", "localhost")


def yasak():
    return JSONResponse({"hata": "Panel sadece eğitmenin bilgisayarından açılabilir."}, status_code=403)


@mcp.custom_route("/panel", methods=["GET"])
async def panel(istek: Request):
    if not sadece_yerel(istek):
        return yasak()
    return HTMLResponse((Path(__file__).parent / "panel.html").read_text(encoding="utf-8"))


@mcp.custom_route("/panel/veri", methods=["GET"])
async def panel_veri(istek: Request):
    if not sadece_yerel(istek):
        return yasak()
    return JSONResponse({**VERI, "adres": f"http://{yerel_ip()}:{PORT}/mcp"})


@mcp.custom_route("/panel/duyuru", methods=["POST"])
async def panel_duyuru_ekle(istek: Request):
    if not sadece_yerel(istek):
        return yasak()
    g = await istek.json()
    if not g.get("metin", "").strip():
        return JSONResponse({"hata": "Duyuru metni boş olamaz."}, status_code=400)
    yeni_id = max((d["id"] for d in VERI["duyurular"]), default=0) + 1
    VERI["duyurular"].append({
        "id": yeni_id, "tarih": datetime.now().strftime("%d.%m.%Y"), "saat": datetime.now().strftime("%H:%M"),
        "ders": g.get("ders", "").strip() or "Genel", "tur": g.get("tur", "genel"), "metin": g["metin"].strip(),
    })
    kaydet()
    log(f"✍️  Eğitmen duyuru yayınladı: {g['metin'].strip()}")
    return JSONResponse({"tamam": True})


@mcp.custom_route("/panel/duyuru/sil", methods=["POST"])
async def panel_duyuru_sil(istek: Request):
    if not sadece_yerel(istek):
        return yasak()
    silinecek = (await istek.json()).get("id")
    VERI["duyurular"] = [d for d in VERI["duyurular"] if d["id"] != silinecek]
    kaydet()
    return JSONResponse({"tamam": True})


@mcp.custom_route("/panel/anket", methods=["POST"])
async def panel_anket_baslat(istek: Request):
    if not sadece_yerel(istek):
        return yasak()
    g = await istek.json()
    secenekler = [s.strip() for s in g.get("secenekler", []) if s.strip()]
    if not g.get("soru", "").strip() or len(secenekler) < 2:
        return JSONResponse({"hata": "Bir soru ve en az iki seçenek gerekli."}, status_code=400)
    VERI["anket"] = {"soru": g["soru"].strip(), "secenekler": secenekler, "oylar": {}, "acik": True}
    kaydet()
    log(f"📊 Eğitmen anket başlattı: {g['soru'].strip()}")
    return JSONResponse({"tamam": True})


@mcp.custom_route("/panel/anket/kapat", methods=["POST"])
async def panel_anket_kapat(istek: Request):
    if not sadece_yerel(istek):
        return yasak()
    if VERI["anket"]:
        VERI["anket"]["acik"] = False
        kaydet()
    return JSONResponse({"tamam": True})


@mcp.custom_route("/panel/sifirla", methods=["POST"])
async def panel_sifirla(istek: Request):
    if not sadece_yerel(istek):
        return yasak()
    VERI_DOSYASI.unlink(missing_ok=True)
    VERI.clear()
    VERI.update(veriyi_yukle())
    log("🧹 Eğitmen tüm verileri sıfırladı.")
    return JSONResponse({"tamam": True})


def yerel_ip():
    """Bu bilgisayarın yerel ağdaki IP adresini bulur (internete bağlanmaz)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    ip = yerel_ip()
    print(f"""
📡 Kampüs Duyuru Servisi başladı.

   Eğitmen paneli (sadece bu bilgisayardan):  http://localhost:{PORT}/panel
   Öğrencilerin bağlanacağı MCP adresi:       http://{ip}:{PORT}/mcp

   Öğrenci komutu:
   python ajan.py --kaynak postgres --duyuru http://{ip}:{PORT}/mcp --ad "Adın"

   Kapatmak için Ctrl+C. Gelen istekler aşağıda görünecek.
""", file=sys.stderr, flush=True)
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        pass
