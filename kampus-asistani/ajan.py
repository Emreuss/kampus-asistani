"""
Kampüs Asistanı Ajanı: LangGraph + LangChain + MCP

Mimari:
    Kullanıcı ──> [ ajan ] ──(araç lazım mı?)──> [ araclar ] ──┐
                     ^                                         │
                     └─────────────── sonuçlar ────────────────┘
                     │
                     └──(araç gerekmiyor)──> Cevap

- Araçları BİZ yazmıyoruz: langchain-mcp-adapters, server.py'deki MCP
  araçlarını otomatik olarak LangChain araçlarına dönüştürüyor.
- Grafiği BİZ kuruyoruz: LangGraph ile iki düğüm ve bir koşullu kenar.

Kullanım:
    python ajan.py                          # Ollama ile sohbet (internetsiz)
    python ajan.py --model anthropic        # Claude ile (internet + ANTHROPIC_API_KEY)
    python ajan.py --model openrouter       # OpenRouter ile (internet + OPENROUTER_API_KEY)
    python ajan.py --soru "Yarın derslerim neler?"   # Tek soru sor ve çık
    python ajan.py --graf                   # Grafiğin Mermaid çizimini yazdır
    python ajan.py --protokol               # Server'daki MCP trafiğini de canlı göster
    python ajan.py --kaynak postgres        # Dosyalar yerine PostgreSQL MCP server'ına bağlan
    python ajan.py --duyuru http://192.168.1.10:8000/mcp --ad "Ayşe"   # + uzak duyuru server'ı
"""

import argparse
import asyncio
import os
import sys
from contextlib import AsyncExitStack
from datetime import date
from pathlib import Path

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

sys.stdout.reconfigure(encoding="utf-8")
if sys.platform == "win32":
    os.system("")  # Windows terminalinde renkleri etkinleştirir

MAVI, SARI, YESIL, GRI, SIFIRLA = "\033[94m", "\033[93m", "\033[92m", "\033[90m", "\033[0m"
GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def metne_cevir(icerik):
    """Mesaj içeriği bazen düz metin, bazen içerik blokları listesi olarak gelir."""
    if isinstance(icerik, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in icerik)
    return str(icerik)


# =====================================================================
# 1) MODEL: Ajanın "beyni". Hangisini kullanacağımızı seçiyoruz.
# =====================================================================

def model_sec(saglayici, model_adi=None):
    if saglayici == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=model_adi or os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
            # Ollama başka bir bilgisayarda çalışıyorsa (örn. hocanın makinesi):
            # OLLAMA_HOST=http://192.168.1.10:11434 python ajan.py
            base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            temperature=0,
        )
    if saglayici == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_adi or "claude-haiku-5-5", temperature=0)
    if saglayici == "openrouter":
        # OpenRouter, yüzlerce modele (OpenAI, Google, Meta, Mistral...) tek bir API anahtarıyla
        # erişim sağlar. API'si OpenAI ile uyumlu olduğu için ChatOpenAI'ı OpenRouter'ın
        # adresine yönlendirmemiz yeterli. Model adları "saglayici/model" biçimindedir.
        anahtar = os.getenv("OPENROUTER_API_KEY")
        if not anahtar:
            raise ValueError("OPENROUTER_API_KEY ortam değişkeni tanımlı değil. "
                             "Anahtarını https://openrouter.ai/keys adresinden alabilirsin.")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_adi or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
            base_url="https://openrouter.ai/api/v1",
            api_key=anahtar,
            temperature=0,
            # İsteğe bağlı: uygulamanın OpenRouter panelinde hangi adla görüneceği
            default_headers={"X-Title": "Kampus Asistani"},
        )
    raise ValueError(f"Bilinmeyen model sağlayıcı: {saglayici}")


def model_adi_bul(model):
    """Ekranda göstermek için modelin adını bulur (sağlayıcıya göre alan adı değişir)."""
    return getattr(model, "model_name", None) or getattr(model, "model", "?")


def sistem_mesaji(ogrenci_adi=None):
    # LLM bugünün tarihini bilmez; "yarın" veya "cuma" gibi ifadeleri
    # tarihe çevirebilmesi için ona söylüyoruz.
    bugun = date.today()
    metin = (
        "Sen bir üniversite öğrencisine yardım eden Kampüs Asistanı'sın. "
        f"Bugün {bugun.isoformat()}, {GUNLER[bugun.weekday()]}. "
        "Menü, ders programı ve ödevlerle ilgili sorularda MUTLAKA araçları kullan, "
        "bilgi uydurma. Hazır araçlarla cevaplanamayan sorular (istatistik, sayma, karşılaştırma) "
        "için sql_sorgula aracın varsa onu kullan. Ders programı sorulduğunda duyurulari_getir "
        "aracın varsa iptal veya değişiklik olup olmadığını onunla da kontrol et. "
        "Kısa ve samimi Türkçe cevaplar ver."
    )
    if ogrenci_adi:
        metin += f" Öğrencinin adı: {ogrenci_adi}. Soru sorarken veya oy verirken bu adı kullan."
    return SystemMessage(content=metin)


# =====================================================================
# 2) GRAF: LangGraph ile ajanın düşünme döngüsünü kuruyoruz.
# =====================================================================

def graf_olustur(model, araclar, ogrenci_adi=None):
    model_araclarla = model.bind_tools(araclar)  # Modele "bu araçların var" diyoruz.

    async def ajan(durum: MessagesState):
        """Düğüm 1: Model mesajlara bakar; ya araç çağırmaya karar verir ya da cevap yazar."""
        # MCP araç sonuçları "içerik blokları listesi" olarak gelir. Bazı sağlayıcılar (OpenRouter
        # üzerindeki bazı modeller gibi) araç mesajında düz metin bekler; bu yüzden metne çeviriyoruz.
        mesajlar = [
            m.model_copy(update={"content": metne_cevir(m.content)})
            if isinstance(m, ToolMessage) and isinstance(m.content, list) else m
            for m in durum["messages"]
        ]
        cevap = await model_araclarla.ainvoke([sistem_mesaji(ogrenci_adi)] + mesajlar)
        return {"messages": [cevap]}

    graf = StateGraph(MessagesState)
    graf.add_node("ajan", ajan)
    graf.add_node("araclar", ToolNode(araclar))  # Düğüm 2: İstenen araçları MCP üzerinden çalıştırır.

    graf.add_edge(START, "ajan")
    # tools_condition: Son mesajda araç çağrısı varsa "araclar"a, yoksa bitişe git.
    graf.add_conditional_edges("ajan", tools_condition, {"tools": "araclar", "__end__": "__end__"})
    graf.add_edge("araclar", "ajan")  # Araç sonuçlarıyla tekrar ajana dön.

    # InMemorySaver: Sohbet geçmişini hatırlar ("onu tamamla" gibi takip sorularına izin verir).
    return graf.compile(checkpointer=InMemorySaver())


# =====================================================================
# 3) ÇALIŞTIRMA: Grafı adım adım akıtıp her adımı ekrana yazıyoruz.
# =====================================================================

async def sor(graf, soru, oturum_id="ders"):
    ayar = {"configurable": {"thread_id": oturum_id}}
    async for adim in graf.astream({"messages": [("user", soru)]}, ayar, stream_mode="updates"):
        for dugum, guncelleme in adim.items():
            for mesaj in guncelleme["messages"]:
                if isinstance(mesaj, AIMessage) and mesaj.tool_calls:
                    for c in mesaj.tool_calls:
                        print(f"{SARI}🧠 [{dugum}] Araç çağırmaya karar verdi → {c['name']}({c['args']}){SIFIRLA}")
                elif isinstance(mesaj, ToolMessage):
                    print(f"{GRI}🔧 [{dugum}] {mesaj.name} sonucu: {metne_cevir(mesaj.content)}{SIFIRLA}")
                elif isinstance(mesaj, AIMessage):
                    print(f"{YESIL}🤖 Asistan:{SIFIRLA} {metne_cevir(mesaj.content)}")


async def guvenli_sor(graf, soru, model):
    """Sınıfta en sık karşılaşılan hatalar için anlaşılır mesajlar gösterir."""
    try:
        await sor(graf, soru)
    except Exception as hata:
        print(f"\n❌ Hata: {type(hata).__name__}: {hata}")
        if model == "ollama":
            print("💡 Ollama çalışıyor mu? Terminalde 'ollama serve' komutunu çalıştır.\n"
                  "   Model indirildi mi? 'ollama list' ile kontrol et, yoksa 'ollama pull qwen2.5:7b'.")
        elif model == "anthropic":
            print("💡 İnternet bağlantını ve ANTHROPIC_API_KEY ortam değişkenini kontrol et.")
        elif model == "openrouter":
            print("💡 İnternet bağlantını ve OPENROUTER_API_KEY'i kontrol et. Hesabında kredi var mı?\n"
                  "   Model adı doğru mu? (örn. openai/gpt-4o-mini) Seçtiğin model araç çağırmayı\n"
                  "   desteklemeli: https://openrouter.ai/models?supported_parameters=tools")


async def main():
    p = argparse.ArgumentParser(description="Kampüs Asistanı Ajanı")
    p.add_argument("--model", default="ollama", choices=["ollama", "anthropic", "openrouter"],
                   help="LLM sağlayıcısı (varsayılan: ollama)")
    p.add_argument("--model-adi", help="Örn. qwen2.5:7b (Ollama), openai/gpt-4o-mini (OpenRouter)")
    p.add_argument("--soru", help="Tek bir soru sor ve çık")
    p.add_argument("--graf", action="store_true", help="Grafın Mermaid çizimini yazdır")
    p.add_argument("--protokol", action="store_true", help="Server'ın gelen/giden MCP mesaj loglarını da göster")
    p.add_argument("--kaynak", default="dosya", choices=["dosya", "postgres"],
                   help="Hangi MCP server'a bağlanılacak: JSON dosyaları veya PostgreSQL")
    p.add_argument("--duyuru", default=os.getenv("KAMPUS_DUYURU_URL"),
                   help="Uzak duyuru server'ının adresi, örn. http://192.168.1.10:8000/mcp")
    p.add_argument("--ad", default=os.getenv("KAMPUS_AD"), help="Adın (soru sorarken ve oy verirken kullanılır)")
    a = p.parse_args()

    # Ajan kodu iki kaynakta da AYNI. Değişen tek şey hangi server'ın başlatıldığı.
    server_dosyasi = {"dosya": "server.py", "postgres": "server_postgres.py"}[a.kaynak]

    # Bağlanılacak MCP server'lar. Her biri farklı bir taşıma katmanı kullanabilir.
    serverlar = {
        # 1) Yerel server: Ajan onu alt süreç olarak başlatır ve stdin/stdout ile konuşur.
        "kampus": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(Path(__file__).parent / server_dosyasi)],
            # Server logları varsayılan olarak kapalı; --protokol ile açılır.
            # os.environ'u da aktarıyoruz, yoksa server saat dilimi gibi ayarları göremez.
            "env": {**os.environ, "KAMPUS_LOG": "detay" if a.protokol else "sessiz"},
        },
    }
    if a.duyuru:
        # En sık yapılan hata: adresin sonundaki /mcp'yi unutmak.
        if not a.duyuru.rstrip("/").endswith("/mcp"):
            a.duyuru = a.duyuru.rstrip("/") + "/mcp"
            print(f"{GRI}💡 Adresin sonuna /mcp eklendi: {a.duyuru}{SIFIRLA}")
        # 2) Uzak server: Zaten başka bir bilgisayarda çalışıyor; ona HTTP ile bağlanırız.
        serverlar["duyuru"] = {"transport": "streamable_http", "url": a.duyuru, "timeout": 10}

    istemci = MultiServerMCPClient(serverlar)

    # Kalıcı oturumlar: Her server'a bir kez bağlanıp tüm sohbet boyunca açık tutuyoruz.
    # (istemci.get_tools() kullansaydık her araç çağrısında yeniden bağlanılırdı.)
    async with AsyncExitStack() as yigin:
        araclar = []
        for ad, ayar in serverlar.items():
            etiket = server_dosyasi if ad == "kampus" else ayar["url"]
            try:
                oturum = await yigin.enter_async_context(istemci.session(ad))
                server_araclari = await load_mcp_tools(oturum)
            except Exception as hata:
                print(f"❌ [{etiket}] MCP server'a bağlanılamadı: {type(hata).__name__}")
                if ad == "duyuru":
                    print("💡 Adres doğru mu (sonu /mcp ile bitmeli)? Eğitmenin bilgisayarıyla aynı ağda mısın?\n"
                          f"   Tarayıcıda {ayar['url']} adresini açmayı dene; bir hata sayfası bile gelirse bağlantı var demektir.")
                return
            print(f"{MAVI}🔌 [{etiket}] {len(server_araclari)} araç yüklendi: "
                  f"{', '.join(t.name for t in server_araclari)}{SIFIRLA}")
            araclar += server_araclari

        if a.duyuru and not a.ad:
            print(f"{GRI}💡 Soru sorarken ve oy verirken adının görünmesi için --ad \"Adın\" ekleyebilirsin.{SIFIRLA}")

        try:
            model = model_sec(a.model, a.model_adi)
        except (ValueError, ImportError) as hata:
            print(f"❌ Model hazırlanamadı: {hata}")
            if isinstance(hata, ImportError):
                print("💡 Paketler eksik olabilir: pip install -r requirements.txt")
            return
        print(f"{MAVI}🧠 Model: {a.model} · {model_adi_bul(model)}{SIFIRLA}")

        # Ajan için iki server'ın araçları tek bir liste; hangisinin nereden geldiğini bilmez bile.
        graf = graf_olustur(model, araclar, a.ad)

        if a.graf:
            print(graf.get_graph().draw_mermaid())
            return
        if a.soru:
            await guvenli_sor(graf, a.soru, a.model)
            return

        print("Çıkmak için 'q' yaz.\n")
        while True:
            # input() beklerken arka plandaki MCP bağlantısı donmasın diye ayrı thread'de soruyoruz.
            soru = (await asyncio.to_thread(input, f"{MAVI}👤 Sen:{SIFIRLA} ")).strip()
            if soru.lower() in ("q", "çık", "exit"):
                break
            if soru:
                await guvenli_sor(graf, soru, a.model)
                print()


if __name__ == "__main__":
    asyncio.run(main())
